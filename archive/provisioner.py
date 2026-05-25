import os
import sys
import subprocess
import shutil
from pathlib import Path

class Provisioner:
    def __init__(self):
        self.base_path = Path.cwd()
        self.venv_path = self.base_path / "venv"
        self.agent_script = self.base_path / "agent.py"
        
        # Determine Python path inside the venv based on OS
        if sys.platform == "win32":
            self.venv_python = self.venv_run_path = self.venv_path / "Scripts" / "python.exe"
        else:
            self.venv_python = self.venv_path / "bin" / "python"

    def create_bootstrap_agent(self):
        """Creates a basic agent.py if one doesn't exist, so the system can start."""
        if not self.agent_script.exists():
            print("🚀 No agent.py found. Creating a Bootstrap Heartbeat Agent...")
            content = """
import time
import datetime
import sys

def main():
    print("=== SYSTEM ONLINE: HEARTBEAT AGENT STARTED ===")
    print(f"Started at: {datetime.datetime.now()}")
    print("Running in monitoring mode. Press Ctrl+C to stop.\\n")
    
    try:
        while True:
            now = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"[{now}] System Status: OK | No new tasks in queue.")
            time.sleep(10)
    except KeyboardInterrupt:
        print("\\n=== SYSTEM OFFLINE: AGENT STOPPED ===")
        sys.exit(0)

if __name__ == '__main__':
    main()
"""
            with open(self.agent_script, "w") as f:
                f.write(content.strip())
            print("✅ Bootstrap agent.py created.")

    def setup_venv(self):
        print("🛠️  Setting up Virtual Environment...")
        if not self.venv_run_path.exists():
            subprocess.run([sys.executable, "-m", "venv", "venv"], check=True)
            print("✅ Virtual Environment created.")
        else:
            print("ℹ️  Virtual Environment already exists.")

    def install_dependencies(self):
        print("📦 Installing dependencies (this may take a minute)...")
        # Upgrade pip first
        subprocess.run([str(self.venv_python), "-m", "pip", "install", "--upgrade", "pip"], check=True)
        
        # Install core requirements
        # Note: In a real scenario, you'd use a requirements.txt
        packages = ["playwright", "chromadb", "langchain", "beautifulsoup4", "python-dotenv"]
        subprocess.run([str(self.venv_python), "-m", "pip", "install"] + packages, check=True)
        
        # Install playwright browsers
        print("🌐 Downloading Playwright browser binaries...")
        subprocess.run([str(self.venv_python), "-m", "playwright", "install", "chromium"], check=True)
        print("✅ Dependencies installed.")

    def launch_agent(self):
        """Launches the agent using the VENV python and hands over control."""
        if not self.venv_python.exists():
            print("❌ Error: Virtual environment python not found. Setup failed.")
            return

        print(f"\n🚀 LAUNCHING AGENT: {self.agent_script.name}")
        print(f"Using Python: {self.venv_python}")
        print("-" * 50)

        try:

            # We use Popen so that the Provisioner can hand over the terminal 
            # and the Agent can run as a persistent process.
            process = subprocess.Popen([str(self.venv_python), str(self.agent_script)])
            process.wait() # Wait for the agent to finish (it won't, until you kill it)
        except KeyboardInterrupt:
            print("\n🛑 Provisioner interrupted. Closing Agent...")
            process.terminate()

    def run(self):
        print("--- STARTING SYSTEM PROVISIONING ---")
        self.create_bootstrap_agent()
        self.setup_venv()
        self.install_dependencies()
        print("--- PROVISIONING COMPLETE ---")
        self.launch_agent()

if __name__ == "__main__":
    provisioner = Provisioner()
    provisioner.run()
