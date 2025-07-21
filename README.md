# Setting Up LLM Service Packages Along with DefaultAzureCredential in Visual Studio Code

We need DefaultAzureCredential as we connect with Azure services for Vector Database for LLM service.

---

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
