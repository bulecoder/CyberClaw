import os
from dotenv import load_dotenv

load_dotenv()

CORE_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.dirname(CORE_DIR)
PROJECT_ROOT = os.path.dirname(PACKAGE_DIR)

WORKSPACE_DIR = os.getenv("CYBERCLAW_WORKSPACE", os.path.join(PROJECT_ROOT, "workspace"))   # 工作路径位于workspace，读取CYBERCLAW_WORKSPACE，它控制数据库、用户画像、office、skill、task.json保存在哪里


DB_PATH = os.path.join(WORKSPACE_DIR, "state.sqlite3")     # 状态机：潜意识与短期记忆
MEMORY_DIR = os.path.join(WORKSPACE_DIR, "memory")         # 显性记忆：Markdown 画像
PERSONAS_DIR = os.path.join(WORKSPACE_DIR, "personas")     # 人设区：系统 Prompt
SCRIPTS_DIR = os.path.join(WORKSPACE_DIR, "scripts")       # 脚本区：自动化武器库
OFFICE_DIR = os.path.join(WORKSPACE_DIR, "office")         # 沙盒工位 唯一被允许执行文件与shell操作的空间
SKILLS_DIR = os.path.join(OFFICE_DIR, "skills")            # 技能卡槽
TASKS_FILE = os.path.join(WORKSPACE_DIR, "tasks.json")

for d in [WORKSPACE_DIR, MEMORY_DIR, PERSONAS_DIR, SCRIPTS_DIR, OFFICE_DIR, SKILLS_DIR]:
    os.makedirs(d, exist_ok=True)   # 配置模块导入时自动创建目录，创建这个目录本身不会限制 Python 权限，CyberClaw进程仍然有当前windows用户的文件访问能力

print(f"🔧 [Config] Workspace 路径已就绪: {WORKSPACE_DIR}")