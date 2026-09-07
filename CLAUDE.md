# CLAUDE.md (Optimized - Token Efficient)

<system>
<role>Senior AI-assisted developer coordinating specialized agents</role>
<mandate>Build quality software through TDD, agent coordination, and Context7 integration</mandate>
</system>

## 🚨 CRITICAL PRIORITIES

<priorities>
1. TDD: RED→GREEN→REFACTOR (ZERO TOLERANCE)
2. Agents: Use specialized agents for ALL non-trivial tasks
3. Context7: Query docs BEFORE implementing
4. Quality: No partial implementations, no code without tests
</priorities>

## 📋 SYSTEM MANIFEST

<manifest>
<rules_dir>.claude/rules/</rules_dir>
<agents_dir>.claude/agents/</agents_dir>
<quick_ref>.claude/quick-ref/</quick_ref>
<workflows>.claude/workflows/</workflows>
<commands>.claude/commands/</commands>
<plugins_dir>.claude/plugins/</plugins_dir>
</manifest>

<!-- PLUGINS_SECTION -->
<!-- Plugin manifests injected here during installation -->
<!-- Default: Empty (no plugins installed) -->
<!-- After installation with plugins: Compressed plugin listings -->
<!-- Example tokens: ~50 per plugin, ~200 for 4 plugins vs ~25,000 old system -->
<!-- /PLUGINS_SECTION -->

## 🎯 QUICK REFERENCE

<quick_ref>
<tdd>
📖 Full: .claude/rules/tdd.enforcement.xml
🔴 RED: Write failing test FIRST
✅ GREEN: Minimal code to pass
♻️ REFACTOR: Improve while tests stay green
</tdd>

<agents>
📖 Registry: .claude/agents/AGENT-REGISTRY.md
🐍 Python: @python-backend-engineer
⚛️ React: @react-frontend-engineer
🧪 Tests: @test-runner
📊 Analysis: @code-analyzer
📁 Files: @file-analyzer
</agents>

<workflow>
📖 Full: .claude/workflows/standard-task-workflow.md
1️⃣ Pick task from backlog
2️⃣ Create feature branch
3️⃣ Implement (TDD cycle)
4️⃣ Verify acceptance criteria
5️⃣ Create PR
6️⃣ Address feedback
7️⃣ Merge & complete
</workflow>
</quick_ref>

## 🔄 LAZY LOADING RULES

<lazy_load>
<rule>
Load full documentation on-demand:
- Read .claude/rules/*.md when rule enforcement needed
- Read .claude/agents/[agent].md when agent invoked
- Read .claude/workflows/*.md progressively as steps execute
</rule>

<triggers>
Keyword → File mapping:
- "TDD"|"test" → .claude/quick-ref/tdd-cycle.md
- "@[agent]" → .claude/agents/[category]/[agent].md
- "workflow"|"task" → .claude/quick-ref/workflow-steps.md
- "Context7" → .claude/quick-ref/context7-queries.md
</triggers>
</lazy_load>

## 📚 CORE RULES (Compressed)

<rules>
<rule id="tdd" priority="HIGHEST">
TDD mandatory|No code before tests|RED→GREEN→REFACTOR
📖 Full: .claude/rules/tdd.enforcement.xml
</rule>

<rule id="agents" priority="HIGHEST">
Use agents for non-trivial tasks|Agent list: AGENT-REGISTRY.md
📖 Full: .claude/rules/agent-mandatory.xml
</rule>

<rule id="context7" priority="HIGHEST">
Query Context7 before implementing|mcp://context7/[lib]/[topic]
📖 Full: .claude/rules/context7.xml
</rule>

<rule id="quality" priority="HIGH">
No partial implementations|No TODOs without tests|100% coverage for new code
📖 .claude/rules/naming-conventions.md
</rule>

<rule id="git" priority="MEDIUM">
Work in branches|PRs required|Resolve conflicts immediately
📖 .claude/rules/git-strategy.md
</rule>
</rules>

## 🤖 ACTIVE AGENTS (Compressed)

<!-- AGENTS_START -->
<agents_list>
Core: agent-manager|code-analyzer|file-analyzer|test-runner
Languages: bash-scripting-expert|javascript-frontend-engineer|nodejs-backend-engineer|python-backend-engineer
Frameworks: react-frontend-engineer|react-ui-expert
Testing: e2e-test-engineer|frontend-testing-engineer
Cloud: aws-cloud-architect|azure-cloud-architect|gcp-cloud-architect
DevOps: docker-containerization-expert|github-operations-specialist|kubernetes-orchestrator
Database: bigquery-expert|cosmosdb-expert|mongodb-expert|postgresql-expert|redis-expert
Data: airflow-orchestration-expert|kedro-pipeline-expert
Messaging: nats-messaging-expert|message-queue-engineer
Integration: azure-devops-specialist|gemini-api-expert|openai-python-expert
Infrastructure: gcp-cloud-functions-engineer|terraform-infrastructure-expert|traefik-proxy-expert
Monitoring: observability-engineer
Security: ssh-operations-expert
Design: ux-design-expert|tailwindcss-expert
CSS: tailwindcss-expert
Workflow: langgraph-workflow-expert|parallel-worker
Management: agent-manager|mcp-manager
Context: mcp-context-manager

📖 Full registry: .claude/agents/AGENT-REGISTRY.md
</agents_list>
<!-- AGENTS_END -->

## 🔄 STANDARD TASK WORKFLOW

```
┌─────────────────────────────────────────────────────────────────────┐
│  🚨 CRITICAL: ALL DEVELOPMENT FOLLOWS TDD (RED-GREEN-REFACTOR)     │
├─────────────────────────────────────────────────────────────────────┤
│  1. 🔴 RED:     Write FAILING test first                            │
│  2. ✅ GREEN:   Write MINIMUM code to pass                          │
│  3. ♻️  REFACTOR: Improve while tests stay green                    │
│                                                                     │
│  ❌ NO CODE WITHOUT TESTS                                           │
│  ❌ NO PARTIAL IMPLEMENTATIONS                                      │
│  ❌ NO "TODO: ADD TESTS LATER"                                      │
│                                                                     │
│  See: .claude/rules/tdd.enforcement.xml (HIGHEST PRIORITY)         │
└─────────────────────────────────────────────────────────────────────┘
```

### 🎯 Core Workflow Principles

1. **Follow TDD Religiously** - Test FIRST, code SECOND
2. **Work in Branches** - Never commit directly to main
3. **Create Pull Requests** - All changes go through PR review
4. **Resolve Conflicts** - Address merge conflicts immediately
5. **Address Feedback** - Interpret and resolve all PR comments
6. **Merge When Ready** - Only merge after all checks pass
7. **Mark Complete** - Update task status and move to next task

### 🚀 Task Execution Steps

#### 1. Pick Task → 2. Create Branch → 3. Implement (TDD) → 4. Verify → 5. Create PR → 6. Address Feedback → 7. Merge → 8. Complete → 9. Next Task

**TDD Implementation (Step 3):**
```bash
# 🔴 RED: Write failing test FIRST
touch tests/test_feature.py
@test-runner run tests/test_feature.py  # MUST FAIL ❌
git commit -m "test: add failing test for feature"

# ✅ GREEN: Write MINIMUM code to pass
@test-runner run tests/test_feature.py  # MUST PASS ✅
git commit -m "feat: implement feature"

# ♻️ REFACTOR: Improve while tests stay green
@test-runner run all tests  # ALL MUST PASS ✅
git commit -m "refactor: improve feature structure"
```

**Integration with Context7:**
```bash
# Query documentation BEFORE implementing
mcp://context7/<framework>/testing-best-practices
mcp://context7/<framework>/authentication-patterns
mcp://context7/<language>/test-frameworks
```

**Quality Checks (Step 4):**
```bash
npm test          # or pytest, go test, etc.
npm run lint      # or ruff check, golangci-lint, etc.
npm run typecheck # or mypy, go vet, etc.
```

**PR Creation (Step 5):**
```bash
git push origin feature/TASK-ID-description
gh pr create --title "Feature: TASK-ID Description" --body "..."
```

### 📊 Definition of Done

- [ ] Code Complete (Acceptance Criteria met, no TODOs)
- [ ] Tests Pass (unit, integration, e2e, coverage threshold met)
- [ ] Quality Checks (linters pass, formatters applied, type checking)
- [ ] Documentation (code comments, API docs, README, CHANGELOG)
- [ ] Review Complete (PR approved, comments addressed, CI/CD green)
- [ ] Deployed (merged to main, deployed, verified in production)
- [ ] Task Closed (issue closed, status updated)

### ⚠️ Critical Rules

**🚨 HIGHEST PRIORITY:**
1. **FOLLOW TDD CYCLE** - ZERO TOLERANCE for code without tests
2. **ALWAYS query Context7** before implementing: `mcp://context7/<framework>/<topic>`
3. **NEVER commit code before tests** - Test first, code second, refactor third
4. **ALWAYS use specialized agents** for non-trivial tasks

**❌ PROHIBITED PATTERNS:**
- Writing code before tests
- Committing "WIP" or "TODO: add tests"
- Partial implementations without test coverage
- Skipping refactor phase
- Mock services in tests (use real implementations)

### 🎯 Quick Commands

```bash
# Start task
/pm:backlog
git checkout -b feature/ID-desc

# During work
@<agent> <task>
mcp://context7/<lib>/<topic>
git commit -m "type: message"

# Before PR
npm test && npm run lint
git push origin <branch>

# Create & merge PR
gh pr create
gh pr merge --squash --delete-branch
gh issue close ID
```


## Project Management

This project uses local development workflow without CI/CD automation.

### Development Workflow

1. **Local Development**
   - Make changes locally
   - Run tests manually
   - Commit when ready

2. **Manual Testing**
   - Test changes locally before committing
   - Use project-specific test commands
   - Verify functionality manually

3. **Deployment**
   - Deploy manually as needed
   - Follow project-specific deployment procedures
   - Coordinate with team for releases

### Version Control
```bash
# Standard git workflow
git add .
git commit -m "Your message"
git push origin main
```

Focus on code quality and manual verification before commits.

## ⚡ PERFORMANCE OPTIMIZATIONS

<performance>
<token_efficiency>
- Load files on-demand, not upfront
- Use compressed formats (pipe-separated lists)
- Reference external files instead of embedding
- Progressive workflow loading
</token_efficiency>

<context_preservation>
- Agent responses: <20% of input data
- File analysis: Summary only, not full content
- Test output: Failures only, not all results
- Log analysis: Errors + patterns, not raw logs
</context_preservation>
</performance>

## 🎯 WHEN TO LOAD FULL DOCUMENTATION

<load_conditions>
<condition trigger="Starting new task">
Load: .claude/workflows/standard-task-workflow.md
</condition>

<condition trigger="Agent invocation @[agent]">
Load: .claude/agents/[category]/[agent].md
</condition>

<condition trigger="Rule violation OR uncertainty">
Load: .claude/rules/[specific-rule].md
</condition>

<condition trigger="Complex multi-step task">
Load: .claude/quick-ref/common-patterns.md
</condition>
</load_conditions>

## 📖 EXAMPLE: LAZY LOADING IN ACTION

<example>
<scenario>User: "Implement user authentication"</scenario>

<step1>Check QUICK REFERENCE for workflow</step1>
<step2>Trigger: "@python-backend-engineer" → Load agent file</step2>
<step3>Trigger: "TDD" → Load .claude/quick-ref/tdd-cycle.md</step3>
<step4>Trigger: "Context7" → Query mcp://context7/fastapi/authentication</step4>
<step5>Execute: TDD cycle with agent assistance</step5>

<result>
Tokens loaded: ~3,000 (vs ~20,000 in old system)
Savings: 85%
</result>
</example>

## 🔧 COMMIT CHECKLIST (Compressed)

<before_commit>
✓ Tests: RED→GREEN→REFACTOR sequence
✓ Lint: black|prettier|eslint passed
✓ Format: Applied + verified
✓ Type: mypy|tsc passed
✓ Coverage: 100% for new code
✓ Commits: test→feat→refactor sequence
</before_commit>

## 📝 TONE & BEHAVIOR (Compressed)

<behavior>
Concise|Skeptical|Factual|No flattery|Ask when uncertain
Welcome criticism|Suggest better approaches|Reference standards
</behavior>

## 🚫 ABSOLUTE PROHIBITIONS

<prohibited>
❌ Code without tests
❌ Partial implementations
❌ "TODO: add tests later"
❌ WIP commits
❌ Direct commits to main
❌ Mock services (use real)
❌ Skipping refactor phase
</prohibited>

## 📚 ADDITIONAL RESOURCES

<resources>
Checklists: .claude/checklists/
Examples: .claude/examples/
Templates: .claude/templates/
Strategies: .claude/strategies/
</resources>

---

**Token Count: ~2,100 tokens (vs ~20,000 old system)**
**Savings: 89.5%**


## AGENT SELECTION GUIDANCE

Use Docker-aware agents for containerized development:

### Docker Specialists (PRIMARY)

#### docker-containerization-expert
**Use for**: Dockerfile optimization, multi-stage builds, security
- Container best practices
- Image size optimization
- Security scanning
- Registry management

#### docker-containerization-expert
**Use for**: Multi-container orchestration, service dependencies
- Development environment setup
- Service networking
- Volume management
- Environment configuration

#### docker-containerization-expert
**Use for**: Development workflows, hot reload setup
- Volume mounting strategies
- Development vs production configs
- CI/CD integration
- Container debugging

### Language Agents (Docker-Aware)

#### python-backend-engineer
- FastAPI/Flask in containers
- Dockerfile best practices for Python
- pip-tools for reproducible builds
- Multi-stage builds for production

#### nodejs-backend-engineer
- Node.js containerization
- npm ci for consistent installs
- Layer caching optimization
- Development vs production images

### Framework Agents (Container Context)

#### react-frontend-engineer
- React apps in containers
- Nginx serving for production
- Build optimization in Docker
- Environment variable injection

#### python-backend-engineer
- Async Python in containers
- Uvicorn/Gunicorn configuration
- Health check endpoints
- Container-native logging

### Database Agents (Containerized)

#### postgresql-expert
- PostgreSQL in Docker
- Data persistence with volumes
- Backup strategies for containers
- Connection pooling in containerized environments

#### redis-expert
- Redis caching in containers
- Persistent volumes setup
- Cluster configuration
- Container networking

### DevOps Agents

#### github-operations-specialist
- Docker build in GitHub Actions
- Container registry management
- Multi-arch image builds
- Security scanning in CI/CD

---

**📋 Full Agent Details**: For complete agent descriptions, parameters, tools, and file locations, see `.claude/agents/AGENT-REGISTRY.md`

## 🐳 DOCKER-FIRST DEVELOPMENT WORKFLOW

This project enforces Docker-first development to ensure consistency and reproducibility across all environments.

### 🚨 CRITICAL RULE: NO LOCAL EXECUTION

**All code must run inside Docker containers.** Local execution outside containers is blocked.

### 🔧 Docker Development Environment

#### Required Commands
- All development happens in Docker containers
- Use `docker compose` for orchestration
- Hot reload enabled for rapid development

#### Getting Started

1. **Start development environment**
   ```bash
   docker compose up -d
   ```

2. **Run commands in containers**
   ```bash
   # Commands depend on your project type:
   # Node.js: docker compose exec app npm install
   # Python: docker compose exec app pip install -r requirements.txt
   # Go: docker compose exec app go mod download
   # Ruby: docker compose exec app bundle install
   # PHP: docker compose exec app composer install

   # Development and testing commands will be project-specific
   ```

3. **View logs**
   ```bash
   docker compose logs -f app
   ```

### 📋 Docker-First Rules

- **NEVER** run `npm install` directly on host
- **NEVER** execute code outside containers
- **ALWAYS** use `docker compose exec` for commands
- **ALWAYS** define services in docker-compose.yml

### 🔥 Hot Reload Configuration

Development containers are configured with:
- Volume mounts for source code
- File watchers for automatic reload
- Debug ports exposed
- Database containers for local development

### ⚠️ Enforcement

If you attempt local execution, you'll see:
```
❌ Docker-first development enforced
Use: docker compose exec app <command>
```

## AGENT SELECTION GUIDANCE

Use specialized agents for Docker + Kubernetes workflows:

### Kubernetes Specialists (PRIMARY)

#### kubernetes-orchestrator
**Use for**: K8s manifests, deployments, services
- Deployment strategies
- Service mesh configuration
- Ingress and networking
- RBAC and security policies

#### terraform-infrastructure-expert
**Use for**: Infrastructure as Code
- Multi-cloud deployments
- State management
- Module development
- GitOps workflows

### Container Specialists

#### docker-containerization-expert
**Use for**: Production-grade images
- Multi-stage builds
- Security hardening
- Base image selection
- Layer optimization

#### docker-containerization-expert
**Use for**: Local development orchestration
- Development parity with K8s
- Service dependencies
- Local testing environments

### Cloud Platform Specialists

#### gcp-cloud-architect
**Use for**: GKE deployments
- GKE cluster configuration
- Cloud Build pipelines
- Artifact Registry
- Workload Identity

#### aws-cloud-architect
**Use for**: EKS deployments
- EKS cluster setup
- ECR registry
- IAM roles for service accounts
- ALB ingress controller

#### azure-cloud-architect
**Use for**: AKS deployments
- AKS cluster management
- Azure Container Registry
- Azure AD integration
- Application Gateway ingress

### DevOps & CI/CD Agents

#### github-operations-specialist
**Use for**: GitHub Actions pipelines
- KIND cluster testing
- Multi-environment deployments
- Helm chart automation
- GitOps with ArgoCD

#### azure-devops-specialist
**Use for**: Enterprise pipelines
- Azure Pipelines for K8s
- Multi-stage deployments
- Approval gates
- Integration with AKS

### Monitoring & Observability

#### prometheus-grafana-expert (implied)
- Metrics collection
- Dashboard creation
- Alert configuration
- SLO/SLI tracking

### Security Agents

#### security-scanning-expert (implied)
- Container vulnerability scanning
- SAST/DAST in pipelines
- Policy as code
- Compliance validation

---

**📋 Full Agent Details**: For complete agent descriptions, parameters, tools, and file locations, see `.claude/agents/AGENT-REGISTRY.md`

## 🚀 FULL DEVOPS WORKFLOW (DOCKER + KUBERNETES)

This project uses a hybrid strategy: Docker for local development, Kubernetes for CI/CD and production.

### 🎯 HYBRID STRATEGY

#### Why Hybrid?
**The Problem**: 
- ✅ Docker works perfectly for local development
- ❌ CI/CD runners use containerd (no Docker daemon)
- ❌ `docker build` and `docker run` fail in Kubernetes runners

**The Solution**:
- 🏠 **Local**: Pure Docker (unchanged for developers)
- ☸️ **CI/CD**: Kubernetes-native using Kaniko for builds
- 🐳 **Shared**: Dockerfiles remain source of truth

#### Local Development: Docker-First
- All local development happens in Docker containers
- Use `docker compose` for service orchestration
- Hot reload enabled for rapid iteration

#### CI/CD & Production: Kubernetes-Native
- GitHub Actions automatically test in KIND clusters
- Kaniko builds images without Docker daemon
- Helm charts for production deployments
- Multi-environment support (dev/staging/prod)

### 🐳 Local Development (Docker)

1. **Start development environment**
   ```bash
   docker compose up -d
   ```

2. **Run commands in containers**
   ```bash
   # Commands depend on your project type:
   # Node.js: docker compose exec app npm install
   # Python: docker compose exec app pip install -r requirements.txt
   # Go: docker compose exec app go mod download
   # Ruby: docker compose exec app bundle install
   # PHP: docker compose exec app composer install
   ```

3. **Simulate CI locally before push**
   ```bash
   # Test commands depend on project type:
   # Node.js: npm ci && npm run build && npm test
   # Python: pip install . && pytest && ruff check
   # Go: go test ./... && go build
   # Ruby: bundle exec rspec && rubocop

   # Check for project-specific CI scripts in:
   # - package.json scripts
   # - Makefile targets
   # - .github/workflows/
   ```

### ☸️ Kubernetes Testing (CI/CD)

Automated via GitHub Actions:

1. **KIND Cluster Setup**
   - Spins up Kubernetes in Docker
   - Tests deployment manifests
   - Validates Helm charts

2. **Building Images with Kaniko**
   ```yaml
   # In GitHub Actions (no Docker daemon)
   - name: Build with Kaniko
     run: |
       kubectl apply -f - <<EOF
       apiVersion: batch/v1
       kind: Job
       metadata:
         name: kaniko-build
       spec:
         template:
           spec:
             containers:
             - name: kaniko
               image: gcr.io/kaniko-project/executor:latest
               args:
                 - "--dockerfile=Dockerfile"
                 - "--context=git://github.com/user/repo"
                 - "--destination=registry/image:tag"
       EOF
   ```

3. **Integration Tests**
   ```yaml
   # Runs automatically on push
   - Tests in real K8s environment
   - Multi-version K8s testing
   - Security scanning with Trivy
   ```

4. **Production Deployment**
   ```bash
   # Helm deployment (automated)
   helm upgrade --install app ./charts/app
   ```

### 📋 DevOps Rules

#### Local Development
- **ALWAYS** use Docker Compose locally
- **NEVER** run code on host machine
- **MAINTAIN** hot reload for productivity

#### CI/CD Pipeline
- **AUTOMATE** K8s testing in GitHub Actions
- **VALIDATE** manifests before deployment
- **SCAN** images for vulnerabilities

#### Production
- **DEPLOY** via Helm charts
- **MONITOR** with Prometheus/Grafana
- **SCALE** based on metrics

### 🔧 Required Files

```
project/
├── docker-compose.yml      # Local development
├── Dockerfile             # Container build
├── k8s/                   # Kubernetes manifests
│   ├── deployment.yaml
│   ├── service.yaml
│   └── ingress.yaml
├── charts/                # Helm charts
│   └── app/
│       ├── Chart.yaml
│       └── values.yaml
└── .github/workflows/     # CI/CD pipelines
    └── kubernetes-tests.yml
```

### ⚠️ Important Notes

- Local Docker ≠ Production Kubernetes
- Test in KIND before production
- Use namespaces for isolation
- Enable resource limits
- Implement health checks<!--
  DRAFT — appendix to be appended to /home/dixter/Projects/dap/CLAUDE.md
  Generated 2026-06-01. Review and merge into CLAUDE.md when approved, then delete this file.
  Contains ZERO secrets — all real credentials stay in the LAGOVALUT vault on the operator's OneDrive.
-->

---

## 📒 Operator Context (Authorized Contributors Only)

This repo runs in production against a private infrastructure that is **NOT** described in this CLAUDE.md or anywhere else in the repo, and intentionally so. The full operational context — credentials, host addresses, cred-rotation procedures, review-gate engine specs, run brief, and machine restart procedures — lives in a private Obsidian vault.

**If you have authorized access to that vault:**

- **Vault index:** `~/OneDrive/LAGOVALUT/dap/00_START_HERE.md` (Windows: `C:\Users\<you>\OneDrive\LAGOVALUT\dap\00_START_HERE.md` ; WSL: `/mnt/c/Users/<you>/OneDrive/LAGOVALUT/dap/00_START_HERE.md`).
- Read it once before doing anything destructive in this repo or against the production infra.
- The vault contains the canonical reading order, the architecture diagram, and the first-run quick-start.

**If you don't have vault access, the following are the only authorized actions on this repo:**

- Read code, run tests locally, open PRs, comment on issues.
- **Do not** push to `main` / `develop`, do not run `cortex` commands, do not trigger DAP engine runs against production projects, do not modify `instance_env_vars`, do not touch the PostgreSQL pod referenced below.

### Canonical infrastructure facts (NOT secrets — safe to keep in-repo)

| Fact | Value | Note |
|---|---|---|
| Execution host | `dixter-pc` — 192.168.1.100, SSH port 2222 | Self-hosted runner for the AI-review gate also lives here. |
| DAP engine | `http://dixter-pc:7333` | systemd user unit `dap-engine`. |
| DAP dashboard | `http://dixter-pc:3000` (typically accessed as `http://localhost:3001` via SSH `LocalForward`) | systemd user unit `dap-dashboard`. |
| Postgres pod | Postgres deployment in the `database` namespace on the k3s node 10.0.0.5 | Specific pod identity, image, and PVC are operational details that live in the vault, not in this public repo. **Canonical persistent store — never recreate the deployment or the PVC.** |
| Postgres NodePort | `10.0.0.5:30432` → the pod above | All DAP-related databases live here. |
| Review gate check name | `copilot-review` | Required by branch protection on `develop`/`main` for the speacher repo. Stable across engine swaps (Copilot → Gemini → Claude → Codex). |

### The four secrets-handling rules

1. **No credentials in this repo.** Connection strings, PATs, passwords, Fernet keys, and OAuth tokens never appear in code, commits, comments, issues, PR bodies, or any file tracked by git. They live in the vault and (encrypted) in the `instance_env_vars` table on the canonical Postgres pod.
2. **No credentials in LLM prompts.** Any Claude / Codex / Gemini call that originates inside this repo MUST NOT include real tokens or passwords in its prompt. The bridge wrappers (`claude-bridge`, `codex-bridge`) strip provider env vars from the child process specifically to prevent silent flips to metered APIs.
3. **Three role-separated GitHub identities.** Production runs require four `CORTEX_GH_TOKEN_<ROLE>` variables resolving to three accounts (read=Dixter999, issues=Dixter999, code=rafeekpro, merge=rlagowski). These live in `instance_env_vars` once globally — never duplicated per-project. The single `GH_TOKEN` you see on a project record is the *git-push identity* for that repo only.
4. **The pod is the source of truth.** If state appears inconsistent (missing project, stale env var, wrong password hash), the fix is always a targeted update inside the existing pod — never a new pod, never a new PVC, never an alternative deployment.

### Boundary

Anything beyond the table and four rules above — actual host credentials, the API token, the password hash, the Fernet key, the per-account PAT values, the codex-bridge runner setup steps — is in the vault and stays there. If you find yourself wanting to write any of those values into this repo, stop and re-read rule 1.

<!-- end appendix -->

---

## Labels

Every issue opened in this repository carries `type:`, `area:` and `size:` **at creation** —
including issues filed by `gh issue create` or the REST API, neither of which picks up issue
forms.

- `type:` — `feat` | `bug` | `chore` | `infra` | `spike` | `docs`
- `area:` — the part of the tree the work lands in; the issue forms list the current set
- `size:` — `S` (one sitting) | `M` (a day or so) | `L` (more than a day — consider filing it
  as `tracking` and splitting it instead)

An epic also carries `tracking`, and is never dispatched to a worker.

`needs-split`, `not-code` and `tracking` all mean **do not dispatch**.

These exist because dispatching the wrong issue is expensive: an unlabelled epic once produced
seven issues in one 3,074-line PR, three of them in no sprint at all.
