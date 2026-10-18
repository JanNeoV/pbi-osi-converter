"""Install the locked lab environment without changing the parent environment."""
import os
from pathlib import Path
import subprocess

lab = Path(__file__).resolve().parents[1]
env = dict(os.environ, UV_PROJECT_ENVIRONMENT=str(lab.parents[1] / ".tmp/semantic-lab-venv"))
subprocess.run(["uv", "sync", "--project", str(lab), "--frozen", "--python", "3.12"], env=env, check=True)

