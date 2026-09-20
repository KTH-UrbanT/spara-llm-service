# Setting Up LLM Service Packages Along with DefaultAzureCredential in Visual Studio Code

## Current Azure deployment configuration

The active `generation.py` pipeline uses the `kthspara` resource. Set
`OPENAI_API_KEY` in `.env` to a key from that resource before starting the service.
Chat and embedding clients use Azure OpenAI v1:
`AZURE_ENDPOINT="https://kthspara.openai.azure.com/openai/v1/"`.
Resource-root URLs are also accepted and normalized to `/openai/v1/`.
Do not use the Foundry project URL. Dated `*_API_VERSION` settings are no longer
used; model version dates are managed by the Azure deployment.
See [Azure v1 documentation](https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle).

| Environment variable | Deployment |
| --- | --- |
| `ROUTER_MODEL_DEPLOYMENT_NAME` | `gpt-5.6-luna` |
| `CONVERSATIONAL_MODEL_DEPLOYMENT_NAME` | `gpt-5.6-luna` |
| `GENERIC_MODEL_DEPLOYMENT_NAME` | `gpt-5.6-terra` |
| `BUILDING_MODEL_DEPLOYMENT_NAME` | `gpt-5.6-terra` |
| `OPENAI_RESPONSE_MODEL_DEPLOYMENT_NAME` | `gpt-6-astra` |
| `AZURE_DEPLOYMENT` | `gpt-5.6-terra` |
| `OPENAI_EMBEDDINGS_MODEL_DEPLOYMENT` | `text-embedding-3-large` |

Set `OPENAI_API_TYPE="azure"`. The specialized SQL layer reads
`AZURE_DEPLOYMENT`; `SPECIALIZED_SQL_DEPLOYMENT_NAME` is not read by that layer.
Deployment names must match the table for the model-specific request options.

`src/agents/completion_options.py` supplies `max_completion_tokens` and low
reasoning effort for GPT-5.6 and GPT-6 Astra, omitting unsupported sampling
parameters. Initial total reasoning-plus-output budgets are 4096 tokens for
classification, conversation, general answers and SQL, and 16384 for building
answers, draft reports and expert summaries. These limits need validation with
real workloads. Existing GPT-4 and o3/o4 options remain available for rollback.

After updating `.env`, restart the Python worker:

```powershell
python .\generation.py
```

For container deployments, rebuild the image because the Dockerfile copies `.env`.

Offline regression checks (from `llm-service`, with pytest installed):

```bash
python -m pytest tests/unit/test_completion_options.py tests/unit/test_generic_agent.py tests/unit/test_specialized_sql_layer.py tests/unit/test_telemetry.py -q
```

Then verify live routing, general answers with retrieval, building intent and SQL,
and report generation. Deployment success alone does not verify API credentials,
quota, request compatibility, or answer quality.

Request parameter reference: [Azure reasoning models](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/reasoning).

We need DefaultAzureCredential as we connect with Azure services for Vector Database for LLM service.

---

## Model-generated clarification questions

Set `CLARIFICATION_MODEL_DEPLOYMENT_NAME="gpt-5.6-sol"` to use the existing Azure
OpenAI v1 connection for the building graph's `request_address` and `clarification`
steps. The dedicated prompt is `src/prompts/clarification_prompt.txt`.

The agent receives the user question, bounded conversation history, the workflow's
clarification reason, and matched recorded facts. It requests one neutral question
through a strict JSON schema. Code validates the focus and question shape, rejects
generated numbers/identifiers, named entities and exact repeated questions, and
preserves canonical building choices and lookup outcomes. These checks reduce risk;
they do not establish semantic correctness or eliminate hallucinations or bias.

An unset model, API failure, refusal, incomplete output, or invalid question uses
the existing clarification fallback. Calls have a 15-second timeout and no SDK
retries. Clarification metadata records `generation_status` and `question_focus`.
The agent cannot resolve identity, fetch records, generate reports, or send email.

This integration applies to existing building-clarification steps. Advisory answer
follow-ups still belong to the answer agents, and report-readiness routing remains
a separate workflow change. Rebuild the LLM service after changing this setting.

## Frontend report downloads

Report requests generate a draft `.txt` artifact in Redis. The assistant reply
includes a `downloadable_report` descriptor; the message service preserves it
through socket delivery and conversation history so the frontend can display
**Download Draft Report**. A briefing written only in chat has no downloadable
artifact: ask for a new report after deploying the routing/delivery fixes.

The browser downloads through the authenticated message-service endpoint
`GET /reports/{report_id}/download/`, using the frontend's `VITE_MS_URL`.
For server deployments this must be the public backend URL at frontend build
time. The production Nginx configuration proxies `/reports/` to the message
service. No server filesystem path is exposed to the browser.

Both services must use the same Redis instance and `DRAFT_REPORT_KEY_PREFIX`.
Artifacts expire after `DRAFT_REPORT_TTL_SECONDS` (default: 1800 seconds), and
Redis data loss also removes stored files. Saving message history preserves the
button descriptor, not the report bytes beyond that expiry.

After changes to routing and report delivery, rebuild both services from the
project root:

```powershell
docker compose -f infra/docker-compose.dev.yml up -d --build llm-service message-service
```

## Step 1: Install Required Python Packages and Azure SDKs

1. Open a terminal in Visual Studio Code.

2. Create a new Python environment. Follow the steps below based on your operating system:

   ### Windows
   1. Open Command Prompt or PowerShell.
   2. Navigate to your project directory.
   3. Create a virtual environment:
      ```bash
      python -m venv .env
      ```
   4. Activate the environment:
      ```bash
      .env\Scripts\activate
      ```

   ### macOS
   1. Open the Terminal.
   2. Navigate to your project directory.
   3. Create a virtual environment:
      ```bash
      python3 -m venv .env
      ```
   4. Activate the environment:
      ```bash
      source .env/bin/activate
      ```

3. Run the following command to install the necessary packages for the language service, including the `DefaultAzureCredential` class:

   ```bash
   pip install -r requirements.txt
   ```

4. Verify the installation of `azure-identity`:

   ```bash
   pip show azure-identity
   ```

   This should display package details if installed successfully.

---

## Step 2: Configure Azure Authentication

The `DefaultAzureCredential` class works by automatically detecting authentication methods based on the environment. It supports several authentication options, such as Azure CLI, managed identity, and environment variables.

### Option 1: Use Azure CLI for Authentication

1. Install the Azure CLI if it's not already installed:
   Follow the [Azure CLI installation guide](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli).

2. Log in to your Azure account:

   ```bash
   az login
   ```

3. Set the correct subscription (if you have multiple subscriptions):

   ```bash
   az account set --subscription "YourSubscriptionID"
   ```

---

## Step 3: Set Up Redis Server

### Windows

#### Install Windows Subsystem for Linux (WSL)
1. Open PowerShell as Administrator.
2. Enable WSL:
   ```bash
   wsl --install
   ```
   This command installs WSL and the default Linux distribution (usually Ubuntu).
3. Restart your computer if prompted.
4. After restarting, open the WSL terminal (e.g., Ubuntu) and set up your Linux distribution by following the on-screen instructions.

#### Install Redis on WSL
1. Open the WSL terminal.
2. Update the package lists:
   ```bash
   sudo apt update
   ```
3. Install Redis:
   ```bash
   sudo apt install redis-server
   ```
4. Start the Redis server:
   ```bash
   sudo service redis-server start
   ```
5. Verify Redis is running:
   ```bash
   redis-cli ping
   ```
   If Redis is running, it will respond with `PONG`.

### macOS

#### Install Redis using Homebrew
1. Open the Terminal.
2. Install Homebrew (if not already installed):
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```
3. Install Redis:
   ```bash
   brew install redis
   ```

#### Start Redis
1. Start the Redis service:
   ```bash
   brew services start redis
   ```
2. Verify Redis is running:
   ```bash
   redis-cli ping
   ```
   If Redis is running, it will respond with `PONG`.

---


## Step 4: Start Up Redis Server

Run the following command in the terminal 
   ```bash
   python redis_pub_sub.py
   ```

