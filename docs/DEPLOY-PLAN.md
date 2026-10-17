# Deploy plan: public HTTPS for the judges (Oct 18-19, 2026)

Status: **PLAN, awaiting approval.** No AWS resource is created until it is approved.
After approval only the commands in section 5 are run; anything else is asked first.
AWS credentials are never printed or stored; values below in `<...>` are filled in at run
time from command output (ids, URLs), never from secrets.

## 1. Hosting options (checked Oct 17, 2026)

| Option | Available to a new account? | HTTPS | IAM role for Bedrock (no static keys) | Long-running process + threads | Est. monthly cost (us-east-1) |
|---|---|---|---|---|---|
| **AWS App Runner** | **No.** Closed to new customers since Apr 30, 2026; AWS points to ECS Express Mode ([availability change](https://docs.aws.amazon.com/apprunner/latest/dg/apprunner-availability-change.html)) | - | - | - | - |
| **Amazon ECS Express Mode** (Fargate + shared ALB) | Yes ([overview](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/express-service-overview.html)) | Yes: ALB + ACM certificate + AWS-provided domain, created for us | Yes: task role (`--task-role-arn`) | Yes: a normal container, 1 task always running | **~$45-60** (2 services, see 3) |
| Lightsail container service | Yes | Yes (default domain) | **No documented way** to give the container an IAM role (only an ECR image-puller role) -> would need static keys | Yes | ~$7-10 |
| EC2 (t4g.small) + Caddy | Yes | Caddy + Let's Encrypt; needs a DNS name (own domain or an IP-based name like sslip.io) | Yes: instance profile | Yes | ~$17 |

**Recommendation: ECS Express Mode.** It is the only managed option left that gives HTTPS
and a public URL out of the box *and* an IAM task role, with no servers to patch: one
create call per service. EC2 + Caddy is about $30/month cheaper but means running and
patching a VM, installing Docker/Caddy, and getting a certificate for a DNS name we
don't have. Lightsail would force static AWS keys, which the rules forbid.

## 2. Architecture

```
judge's browser --HTTPS--> [shared ALB, AWS-provided domains, ACM certs]
                              |-- pm-sim    (simulator, Fargate ARM64, 1 task)  --task role--> Bedrock Nova Pro
                              |-- pm-server (MCP server, Fargate ARM64, 1 task)
pm-sim --HTTPS (public URL, OAuth bearer)--> pm-server /mcp
```

* Two Express Mode services from **one image** (`APP=server` or `APP=simulator`), sharing
  one internet-facing ALB in the default VPC. `min = max = 1` task each: state is in
  memory, so it must never scale out.
* **pm-server**: `/mcp`, OAuth (`/authorize`, `/token`, both `.well-known` documents),
  `/privacy`, `/terms`, `/addon-assets/*`, `/healthz`. Local-file backend with the
  fictional Sam data seeded at container start. No task role (needs no AWS access).
* **pm-sim**: the simulator, registered as the OAuth client. Task role allowed only
  Bedrock Converse on Nova Pro.

### What happens on restart / redeploy (ephemeral by design)

| State | Where it lives | After a task restart or redeploy |
|---|---|---|
| Memory file | `/data/memories.md` in the container | **Reset** to the fresh Sam seed (dates relative to that day) |
| OAuth tokens (hashes) | `/data/tokens.json` in the server container | **Gone**: the simulator must be linked again |
| Simulator link + conversations | simulator process memory | **Gone**: judges click *Link account* again |
| Daily Bedrock counter, rate limits | simulator process memory | Reset |

Judges see a shared demo: one simulator, one linked account, one memory file. Anyone can
press *Reset demo* (behind the passcode) to restore Sam. Documented in docs/TESTING.md.

## 3. Cost estimate (us-east-1, per month, 730 h)

| Item | Basis | $/month |
|---|---|---|
| Fargate, 2 tasks x 0.25 vCPU / 0.5 GB ARM64 | $0.03238/vCPU-h, $0.00356/GB-h ([Fargate pricing](https://aws.amazon.com/fargate/pricing/)) | 14.4 |
| Application Load Balancer (shared) | $0.0225/h + ~0.1 LCU ([ELB pricing](https://aws.amazon.com/elasticloadbalancing/pricing/)) | 17-18 |
| Public IPv4: ALB (2-6 AZ addresses) + 2 tasks | $0.005/h each ([VPC pricing](https://aws.amazon.com/vpc/pricing/)) | 15-29 |
| CloudWatch Logs (14-day retention), ECR storage | a few hundred MB | ~1 |
| SSM Parameter Store (standard SecureString) | free tier | 0 |
| Bedrock Nova Pro | ~3-4k input + ~100 output tokens per model call; capped at 300 calls/day (section 4) | 0-~30 worst case, likely < 2 |
| **Total** | | **~$48-65 (likely ~$50)** |

Judging window Oct 19 - Dec 3 (about 1.5 months): **~$75-100**. Nova Pro per-token
prices to be confirmed with `aws pricing get-products` after `aws login`. A smaller bill is
possible later by putting the simulator inside the server's service (one task, one public
IP) or by moving to EC2 + Caddy; not proposed now.

## 4. Code changes before deploying (local, no AWS)

1. **Container**: `Dockerfile` (python:3.13-slim, non-root user, port 8080, `HEALTHCHECK`
   on `/healthz`), `.dockerignore` (no `.venv`, `.oauth`, `.git`, `proof/`, keys),
   `deploy/entrypoint.sh` (`APP=server`: seed `/data/memories.md`, run `server.py`;
   `APP=simulator`: run the simulator).
2. **Simulator protection** (all env-configured, tested offline):
   * Judge passcode gate (`SIM_PASSCODE`): a small sign-in page; a signed, HttpOnly,
     Secure, SameSite=Lax session cookie (`SIM_SESSION_SECRET`); every page/API/OAuth route
     needs it; failed attempts rate-limited.
   * Rate limits: per client IP (from the ALB's `X-Forwarded-For`, rightmost entry) e.g.
     10 utterances/min and 120/day, plus a global 60/min.
   * Daily cap on Bedrock calls (`SIM_DAILY_MODEL_CALLS`, default 300). Over the cap, a
     polite spoken reply ("The demo has reached today's limit; please try again tomorrow")
     and no Bedrock call.
   * *Reset demo* and *Unlink* require the passcode session (replaces `SIM_ADMIN_TOKEN`).
   * `/healthz` for the ALB health check (no passcode).
   * Refuse to start on a public host without `SIM_PASSCODE` and `SIM_SESSION_SECRET`.
3. **Server behind the ALB**: rate-limit sign-ins by the *rightmost* `X-Forwarded-For`
   entry (the one the ALB adds; today we take the leftmost, which a client can forge).
4. **PRIVACY_CONTACT** set from your answer (question 3 below).

## 5. AWS commands (run only after approval, in this order)

Names: prefix `alexa-memory`, region `us-east-1`, tag `project=alexa-memory-mcp` on
everything that takes tags. `<ACCOUNT>` comes from `aws sts get-caller-identity`.

**5.0 Read-only checks**: `aws sts get-caller-identity` (must be the IAM user, not root),
`aws bedrock get-inference-profile --inference-profile-identifier us.amazon.nova-pro-v1:0`
(to list the model ARNs it routes to), `aws ec2 describe-vpcs --filters Name=is-default,Values=true`
and `describe-subnets` (default VPC with >= 2 public subnets), `aws pricing get-products`
for Nova Pro.

**5.1 Image registry**
* `aws ecr create-repository --repository-name alexa-memory-mcp --image-scanning-configuration scanOnPush=true --tags ...`
* `aws ecr put-lifecycle-policy` (keep the last 5 images).

**5.2 Build and push** (needs a container builder, decision 2): build `linux/arm64`, tag
with the git commit, `docker push <ACCOUNT>.dkr.ecr.us-east-1.amazonaws.com/alexa-memory-mcp:<sha>`.

**5.3 Secrets** (SSM Parameter Store, SecureString, default `aws/ssm` key; values generated
locally and piped in, never echoed):
`/alexa-memory-mcp/oauth-client-secret`, `/alexa-memory-mcp/owner-passphrase-hash`,
`/alexa-memory-mcp/sim-passcode`, `/alexa-memory-mcp/sim-session-secret`.
The plain judge passcode and demo owner passphrase are written once to
`~/.config/alexa-memory-mcp/demo-secrets.txt` (mode 0600, outside the repo) for you to
copy into Devpost's private testing instructions.

**5.4 IAM roles**
* `alexa-memory-ecs-execution` (trust `ecs-tasks.amazonaws.com`):
  `AmazonECSTaskExecutionRolePolicy` + inline `ssm:GetParameters` on
  `arn:aws:ssm:us-east-1:<ACCOUNT>:parameter/alexa-memory-mcp/*`.
* `alexa-memory-ecs-infrastructure` (trust `ecs.amazonaws.com`):
  `AmazonECSInfrastructureRoleforExpressGatewayServices`.
* `alexa-memory-sim-task` (trust `ecs-tasks.amazonaws.com`), inline policy, nothing else:
  ```json
  {"Version": "2012-10-17", "Statement": [{
    "Effect": "Allow", "Action": "bedrock:InvokeModel",
    "Resource": [
      "arn:aws:bedrock:us-east-1:<ACCOUNT>:inference-profile/us.amazon.nova-pro-v1:0",
      "arn:aws:bedrock:<each region the profile routes to>::foundation-model/amazon.nova-pro-v1:0"
    ]}]}
  ```
  (Converse is authorized by `bedrock:InvokeModel`; the region list comes from 5.0.)
* pm-server gets **no** task role.

**5.5 Services** (`aws ecs create-express-gateway-service`, ARM64, `cpu 256`, `memory 512`,
`containerPort 8080`, `healthCheckPath /healthz`, `scalingTarget min=max=1`):
* `pm-server`: env `APP=server HOST=0.0.0.0 PORT=8080 MEMORY_BACKEND=local
  LOCAL_MEMORY_PATH=/data/memories.md OAUTH_TOKEN_STORE=/data/tokens.json DEMO_ADMIN=1
  OAUTH_TRUST_PROXY=1 OAUTH_CLIENT_ID=simulated-alexa PRIVACY_CONTACT=<your value>`;
  secrets `OAUTH_CLIENT_SECRET`, `OWNER_PASSPHRASE_HASH` from SSM.
* `pm-sim`: env `APP=simulator SIM_HOST=0.0.0.0 SIM_PORT=8080 SIM_BRAIN=bedrock
  BEDROCK_MODEL_ID=us.amazon.nova-pro-v1:0 AWS_REGION=us-east-1 OAUTH_CLIENT_ID=simulated-alexa`;
  secrets `OAUTH_CLIENT_SECRET`, `SIM_PASSCODE`, `SIM_SESSION_SECRET`; task role
  `alexa-memory-sim-task`.

**5.6 Wire the URLs** (known only after 5.5): `aws ecs update-express-gateway-service` on
pm-server with `MCP_RESOURCE_URI=https://<server-domain>/mcp`, `ALLOWED_HOSTS=<server-domain>`,
`OAUTH_REDIRECT_URIS=https://<sim-domain>/oauth/callback`; on pm-sim with
`MCP_SERVER_URL=https://<server-domain>/mcp`, `SIM_PUBLIC_URL=https://<sim-domain>`.

**5.7 Logs**: `aws logs put-retention-policy --retention-in-days 14` on both service log groups.

**5.8 Optional cost guard** (decision 4): `aws budgets create-budget` at $80/month with an
e-mail alert at 80 %.

## 6. Verification after deploy (no new resources)

* Smoke: MCP Inspector `tools/list` with an OAuth token obtained via
  `scripts/oauth_token.py` against the public URL; `POST /mcp` without a token -> 401 and no
  `WWW-Authenticate`; both metadata documents; `/privacy`, `/terms`, all
  `/addon-assets/*` files; simulator `/healthz`, passcode gate, rate limit and cap
  behaviour.
* `scripts/bench.py` from this Mac -> `proof/latency-public.txt` (p95 per tool, includes
  the internet round trip).
* `scripts/make_addon.py --base-url https://<server-domain>`.
* Conversations 1-5 through the public simulator with Nova Pro (`scripts/eval_bedrock.py`
  pointed at the public server, plus one manual run in the browser).
* Docs: docs/TESTING.md for judges, README live URLs, ALEXA-READINESS, CLAUDE.md, FRICTION_LOG.

## 7. Teardown after judging (results ~Dec 3)

```bash
aws ecs delete-express-gateway-service --service-arn <pm-sim arn>
aws ecs delete-express-gateway-service --service-arn <pm-server arn>
#   Express Mode deprovisions the shared ALB, target groups, listener rules and its
#   ACM certificates when no Express services remain; verify with:
aws elbv2 describe-load-balancers        # expect none left from Express Mode
aws acm list-certificates                # expect none left from Express Mode
aws ecs delete-cluster --cluster default # only if Express created it and it's empty
aws logs delete-log-group --log-group-name <each service log group>
aws ecr delete-repository --repository-name alexa-memory-mcp --force
aws ssm delete-parameters --names /alexa-memory-mcp/oauth-client-secret \
  /alexa-memory-mcp/owner-passphrase-hash /alexa-memory-mcp/sim-passcode /alexa-memory-mcp/sim-session-secret
#   IAM: detach managed policies, delete inline policies, then delete the three roles
aws iam detach-role-policy --role-name alexa-memory-ecs-execution --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy
aws iam delete-role-policy --role-name alexa-memory-ecs-execution --policy-name ssm-read
aws iam delete-role --role-name alexa-memory-ecs-execution
aws iam detach-role-policy --role-name alexa-memory-ecs-infrastructure --policy-arn arn:aws:iam::aws:policy/service-role/AmazonECSInfrastructureRoleforExpressGatewayServices
aws iam delete-role --role-name alexa-memory-ecs-infrastructure
aws iam delete-role-policy --role-name alexa-memory-sim-task --policy-name bedrock-nova-pro
aws iam delete-role --role-name alexa-memory-sim-task
aws budgets delete-budget --account-id <ACCOUNT> --budget-name alexa-memory-mcp   # if created
#   Leftover check: anything still tagged project=alexa-memory-mcp
aws resourcegroupstaggingapi get-resources --tag-filters Key=project,Values=alexa-memory-mcp
```

Service-linked roles that ECS, ELB and Application Auto Scaling create are shared AWS roles
and are left in place (no cost). The exact ARNs/names are recorded in docs/DEPLOY-LOG.md
during deployment so teardown can be copied.

## 8. Decisions needed from you

1. **Hosting**: ECS Express Mode (recommended, ~$50/month) or EC2 + Caddy (~$17/month,
   more to operate, needs a DNS name)?
2. **Building the image**: this Mac has no Docker. Either install **Colima + Docker CLI with
   Homebrew** (free, local, recommended), or build in **AWS CodeBuild** (one more AWS
   resource and an S3 source bucket).
3. **PRIVACY_CONTACT**: the contact shown on /privacy and /terms (an e-mail or URL).
4. **Budget alert**: create an $80/month AWS Budget with an e-mail alert? If yes, which e-mail?
5. **Limits**: OK with 10 utterances/min and 120/day per IP, 60/min global, 300 Bedrock calls/day?
6. **Run `aws login`** before approval: the CLI session has expired again.
