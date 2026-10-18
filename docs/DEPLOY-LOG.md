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
