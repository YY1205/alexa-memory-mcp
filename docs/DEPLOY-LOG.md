# Deploy log (public demo)

Everything created for the public demo, in order, so teardown (docs/DEPLOY-PLAN.md
section 7) can be copied. Region `us-east-1`; account id shown as `<ACCOUNT>`; all
resources tagged `project=alexa-memory-mcp` where the service supports tags.

## 2026-10-18

| Step | Resource | Name / id | Notes |
|---|---|---|---|
| 5.0 | (read-only) | identity `user/yue-admin` (IAM user, not root); plan **PAID** (upgraded by the owner), $119.48 credits; existing budget "My Monthly Cost Budget" ($10, kept as is); Nova Pro profile routes to us-east-1/us-east-2/us-west-2; default VPC subnets us-east-1a `subnet-04387650041eb0a78`, us-east-1b `subnet-003c6e46316ac95c4` | |
| 5.1 | ECR repository | `alexa-memory-mcp` | scan on push; lifecycle: keep last 5 images |
| 5.2 | ECR image | `alexa-memory-mcp:e32e445` (arm64) | `docker logout` right after the push: no registry token left on disk |
| 5.3 | SSM SecureString | `/alexa-memory-mcp/oauth-client-secret`, `/alexa-memory-mcp/owner-passphrase-hash`, `/alexa-memory-mcp/sim-passcode`, `/alexa-memory-mcp/sim-session-secret` | values generated locally, sent via a temporary 0600 file; judge passcode + demo owner passphrase only in `~/.config/alexa-memory-mcp/demo-secrets.txt` (0600) |
| 5.4 | IAM role | `alexa-memory-ecs-execution` | trust `ecs-tasks.amazonaws.com` (documented form, no condition); `AmazonECSTaskExecutionRolePolicy` + inline `ssm-read` |
| 5.4 | IAM role | `alexa-memory-ecs-infrastructure` | trust `ecs.amazonaws.com`; `AmazonECSInfrastructureRoleforExpressGatewayServices` |
| 5.4 | IAM role | `alexa-memory-sim-task` | trust `ecs-tasks.amazonaws.com` + `aws:SourceAccount`; inline `bedrock-nova-pro` (InvokeModel on the profile + 3 model ARNs) |
| (approved extra) | IAM service-linked roles | `AWSServiceRoleForECS`, `AWSServiceRoleForElasticLoadBalancing`, `AWSServiceRoleForApplicationAutoScaling_ECSService` | Created after `Role is not valid`; **not the cause** (see below). Standard AWS-managed roles, free; left in place at teardown |
| 5.5 | ECS Express service | `pm-server` (cluster `default`), `https://pm-2cbfc2377dac4f9b8a15cc6219028368.ecs.us-east-1.on.aws` | ARM64, 0.25 vCPU / 512 MB, 1 task, subnets us-east-1a + 1b, no task role |
| 5.5 | ECS Express service | `pm-sim` (cluster `default`), `https://pm-eed93c7c206e48b49a65ec4b4273adb3.ecs.us-east-1.on.aws` | same sizing; task role `alexa-memory-sim-task` |
| 5.5 | (created by Express Mode) | cluster `default`, shared internet-facing ALB, target groups, listener rules, ACM certificates, security groups, auto-scaling targets/policies, log groups | deleted with the services (verify at teardown) |
| 5.6 | update | pm-server: `MCP_RESOURCE_URI`, `ALLOWED_HOSTS`, `OAUTH_REDIRECT_URIS`; pm-sim: `MCP_SERVER_URL`, `SIM_PUBLIC_URL` | canary redeploys |
| 5.7 | CloudWatch Logs retention | `/aws/ecs/default/pm-server-5b4f`, `/aws/ecs/default/pm-sim-9f80` | 14 days |
| 5.8 | AWS Budget | `alexa-memory-mcp` | $80/month COST; e-mail yuanyue116@gmail.com at ACTUAL > 80 % and FORECASTED > 100 %. Existing "My Monthly Cost Budget" ($10) untouched |

### Incident: `Role is not valid` (5.5)

Three create attempts failed with `ClientException: Role is not valid`. Cause: the
commands ran in zsh, where `$ACCT:r` and `$ACCT:u` are variable *modifiers*, so
`arn:aws:iam::$ACCT:role/...` expanded to `arn:aws:iam::<account>ole/...`. Found via a
mangled ARN in an `iam simulate-principal-policy` error. Fixed by building the inputs in
Python and printing every ARN before the call. Side effects, both harmless: the execution
role's trust policy lost the extra `aws:SourceAccount` condition (now the documented
form), and three service-linked roles were created that turned out not to be needed for the
fix. The inline policies written in 5.4 were checked and are correct.

## Verification (Oct 18, after deploy)

| Check | Result | Proof |
|---|---|---|
| Rollout | both services 1/1 running, deployments COMPLETED; updated revision serves the https resource URI | |
| 401 without token, no `WWW-Authenticate` | PASS (HTTP/2 401, body has `error`, `error_description`, `message`) | proof/inspector-tools-list-public.txt |
| Protected resource + authorization server metadata | PASS (resource `https://pm-2cbfc2377dac4f9b8a15cc6219028368.ecs.us-east-1.on.aws/mcp`, S256, no registration endpoint) | |
| /privacy (contact), /terms, /addon-assets (6 icons + carousel) | PASS | |
| Simulator health, passcode gate, API behind the gate | PASS | |
| MCP Inspector tools/list with an OAuth token | PASS (6 tools) | proof/inspector-tools-list-public.txt |
| Latency from this Mac (incl. internet) | p95 74-105 ms per tool | proof/latency-public.txt |
| Conversations 1-5 through the public simulator, Nova Pro | 5/5 PASS; MCP 6-15 ms per call inside AWS; model 1.1-2.0 s per turn | proof/public-conversations.txt |

The public simulator was left linked and reset to Sam's seed for the judges.

## Redeploy (Oct 19-20): current main `668b347` (memory_overview + export)

| Step | Resource | Name / value | Notes |
|---|---|---|---|
| R.0 | (read-only) | identity `user/yue-admin` (IAM user, not root) | |
| R.1 | ECR image | `alexa-memory-mcp:668b347` (arm64, built with Colima) | `docker logout` right after the push |
| R.2 | update | `pm-server`, `pm-sim`: image only | roles, env (13 / 11 vars), secrets (2 / 3), sizing, subnets unchanged; no new AWS resources |
| R.3 | (read-only) | rollout | both deployments COMPLETED; running tasks verified on `668b347` directly (DescribeTaskDefinition is not allowed on Express-managed task definitions) |

### Verification (after the redeploy)

| Check | Result | Proof |
|---|---|---|
| 401 without token, no `WWW-Authenticate`; both metadata docs; /privacy, /terms, /addon-assets; TLS; simulator health, passcode gate, API behind the gate | 11/11 PASS | |
| MCP Inspector tools/list with an OAuth token | PASS (8 tools, incl. `memory_overview`, `export_memory`) | proof/inspector-tools-list-public.txt |
| Conversations 0-5 and 7 through the public simulator, Nova Pro (6 skipped: needs a hand edit of the file) | 6/7 PASS; #4 FAIL once (the model answered the review itself, then claimed a save after a `confirm_memory` error); #4 re-run twice: 2/2 PASS. MCP 4-49 ms per call | proof/public-conversations.txt |

Known issue, not fixed in this deployment: Nova Pro sometimes calls `resolve_review` without
waiting for the user's answer in conversation 4.

The public simulator was left linked (memory.read + memory.write) and reset to Sam's seed
(10 active records) for the judges.
