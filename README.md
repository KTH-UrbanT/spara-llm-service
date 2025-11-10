

## Step 1: Install Required Python Packages and Azure SDKs

1. Open a terminal in Visual Studio Code.

2. Create a new Python environment. Follow the steps below based on your operating system:

   ### Windows
   1. Open Command Prompt or PowerShell.
   2. Navigate to your directory where you did git clone. 
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
   2. Navigate to your directory where you did git clone.
   3. Create a virtual environment:
      ```bash
      python3 -m venv .env
      ```
   4. Activate the environment:
      ```bash
      source .env/bin/activate
      ```

3. Run the following command to install the necessary packages for the language service:

   ```bash
   pip install -r requirements.txt
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

### currently we are running only using pinecone. 

Get the updated env file from notion. 

Run the following command in the terminal 
   ```bash
   python redis_pub_sub_pinecone.py
   ```

### DONOT RUN THE ORIGINAL SERVICE

Get the updated env file from notion. 

Run the following command in the terminal 
   ```bash
   python redis_pub_sub.py
   ```