# Setting Up DefaultAzureCredential in Visual Studio Code

## Step 1: Install Required Azure SDKs

1. Open a terminal in Visual Studio Code.

2. Run the following command to install the `azure-identity` package, which includes the `DefaultAzureCredential` class:

   ```bash
   pip install azure-identity
   ```

3. If you're using a `requirements.txt` file, add this line to it:

   ```
   azure-identity
   ```

4. Verify installation by running:

   ```bash
   pip show azure-identity
   ```

   This should display package details if installed successfully.

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

### Option 2: Use Environment Variables for Authentication

1. Set the following environment variables in your system or within Visual Studio Code for service principal-based authentication:

   - `AZURE_CLIENT_ID`: Your Azure client ID.
   - `AZURE_TENANT_ID`: Your Azure tenant ID.
   - `AZURE_CLIENT_SECRET`: Your Azure client secret.

2. You can add these variables to a `.env` file or your system environment settings.
