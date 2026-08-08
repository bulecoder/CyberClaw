import os
import typer
import questionary
import logging
from rich.console import Console
from rich.panel import Panel
from rich.status import Status
from dotenv import set_key, unset_key

from cyberclaw.core.environment import (
    ConfigurationError,
    ENV_PATH,
    load_project_env,
)
from cyberclaw.core.context import ContextPolicy, ContextPolicyError
from cyberclaw.core.provider import OPENAI_COMPATIBLE_PROVIDERS, get_provider
from cyberclaw.core.runtime import AgentRunLimits, RuntimeLimitConfigError
from langchain_core.messages import HumanMessage

app = typer.Typer(help="CyberClaw - 极客专属的赛博智能终端")
console = Console()

cyber_style = questionary.Style([
    ('qmark', 'fg:#8d52ff bold'),       
    ('question', 'fg:#00ffff bold'),    
    ('answer', 'fg:#8d52ff bold'),      
    ('pointer', 'fg:#00ffff bold'),     
    ('highlighted', 'fg:#00ffff bold'), 
    ('selected', 'fg:#00ffff'),
    ('instruction', 'fg:#808080 dim'),  
])

@app.command("config")
def config_wizard():
    try:
        load_project_env()
    except ConfigurationError as exc:
        console.print(f"[bold red]无法读取现有配置：[/bold red]{exc}")
        return

    console.clear()
    console.print(Panel(
        "👾 Welcome to [bold #8d52ff]CyberClaw[/bold #8d52ff]...\n\n☁️[dim] 请完成模型配置，我们将把密钥安全固化在本地。[/dim]", 
        title="[bold white]✦  CyberClaw Config[/bold white]", 
        border_style="#8d52ff"
    ))
    provider_raw = questionary.select(
        "选择你的模型提供商 (Provider):",
        choices=["openai", "anthropic", "aliyun (openai compatible)","tencent (openai compatible)", "z.ai (openai compatible)", "other (openai compatible)", "ollama"],
        style=cyber_style,
        instruction="(按上下键选择，回车确认)"
    ).ask()

    if not provider_raw:
        console.print("[dim #8d52ff]✦   录入中断，CyberClaw 配置已取消。[/dim #8d52ff]")
        return

    provider = provider_raw.split(" ")[0].strip()
    is_openai_compatible = "openai" in provider_raw.lower()     # openai 兼容性

    model_name = questionary.text(
        "输入指定的模型型号 (如 gpt-4o-mini, qwen-max, glm-4 等):",
        style=cyber_style
    ).ask()

    if model_name is None:
        console.print("[dim #8d52ff]✦   录入中断，CyberClaw 配置已取消。[/dim #8d52ff]")
        return

    api_key = ""
    env_key = ""
    if provider != "ollama":
        if is_openai_compatible:
            env_key = "OPENAI_API_KEY"
        elif provider == "anthropic":
            env_key = "ANTHROPIC_API_KEY"

        api_key = questionary.password(
            f"输入你的 {env_key} (对应 {provider_raw}):",
            style=cyber_style
        ).ask()

        if api_key is None:
            console.print("[dim #8d52ff]✦   录入中断，CyberClaw 配置已取消。[/dim #8d52ff]")
            return

    base_url = ""
    if provider in ["openai", "anthropic"]:
        base_url = questionary.text(
            f"输入 {provider} 代理 Base URL (直连请直接回车跳过):",
            style=cyber_style
        ).ask()
    elif provider == "ollama":
        base_url = questionary.text(
            "输入 Ollama Base URL (默认 http://localhost:11434，直接回车跳过):",
            style=cyber_style
        ).ask()
    else:
        base_url = questionary.text(
            "输入兼容 Base URL (不填直接回车将使用官方默认地址):",
            style=cyber_style
        ).ask()

    if base_url is None:
        console.print("[dim #8d52ff]✦   录入中断，CyberClaw 配置已取消。[/dim #8d52ff]")
        return

    effective_base_url = base_url.strip()
    if provider == "other" and not effective_base_url:
        effective_base_url = os.getenv("OPENAI_API_BASE", "").strip()

    console.print("\n[dim]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/dim]")

    with Status(f"[bold #8d52ff]正在连接 {provider.upper()} 引擎并发送探测包...[/bold #8d52ff]", spinner="dots", spinner_style="#00ffff"):
        try:
            llm = get_provider(
                provider_name=provider,
                model_name=model_name,
                api_key=api_key or None,
                base_url=effective_base_url or None,
            )
            llm.invoke([HumanMessage(content="回复我'收到'。")])

            console.print(" [bold #00ffff][ 配置成功!][/bold #00ffff]")
            
        except Exception as e:

            console.print(f" [bold #8d52ff][ 配置失败!][/bold #8d52ff]  无法连接到模型，请检查 Key、Base URL、模型型号 或 网络！\n[dim]错误信息: {str(e)}[/dim]")
            return


    ENV_PATH.touch(exist_ok=True)

    logging.getLogger("dotenv.main").setLevel(logging.ERROR)

    env_path = str(ENV_PATH)
    unset_key(env_path, "OPENAI_API_BASE", encoding="utf-8-sig")
    unset_key(env_path, "ANTHROPIC_BASE_URL", encoding="utf-8-sig")
    unset_key(env_path, "OLLAMA_BASE_URL", encoding="utf-8-sig")

    if env_key and api_key:
        set_key(env_path, env_key, api_key, encoding="utf-8-sig")

    if effective_base_url:
        if is_openai_compatible:
            set_key(env_path, "OPENAI_API_BASE", effective_base_url, encoding="utf-8-sig")
        else:
            set_key(
                env_path,
                f"{provider.upper()}_BASE_URL",
                effective_base_url,
                encoding="utf-8-sig",
            )

    set_key(env_path, "DEFAULT_PROVIDER", provider, encoding="utf-8-sig")
    set_key(env_path, "DEFAULT_MODEL", model_name.strip(), encoding="utf-8-sig")

    console.print(Panel(
        f"配置已保存至 [#8d52ff]{ENV_PATH}[/#8d52ff]\n"
        f"当前默认提供商: [#8d52ff]{provider}[/#8d52ff] | 模型: [#8d52ff]{model_name}[/#8d52ff]\n\n"
        f"👉 输入 [bold #00ffff]cyberclaw run[/bold #00ffff] 即可启动系统！",
        border_style="#00ffff"
    ))

def _show_boot_error(details: str = "未检测到完整的 Provider、模型或 API 配置。"):
    console.print(Panel(
        "[bold #00ffff]CyberClaw未完成配置![/bold #00ffff]\n\n"
        f"[#8d52ff]{details}请检查配置或重新执行：[/#8d52ff]\n"
        "[bold #00ffff]cyberclaw config[/bold #00ffff]",
        title="[bold #8d52ff]⚠️ Boot Sequence Failed[/bold #8d52ff]",
        border_style="#8d52ff"
    ))


@app.command("run")
def run_agent():
    try:
        load_project_env()
    except ConfigurationError as exc:
        _show_boot_error(f"{exc}\n")
        raise typer.Exit(code=1)

    provider = os.getenv("DEFAULT_PROVIDER", "").strip().lower()
    model = os.getenv("DEFAULT_MODEL", "").strip()
    if not provider or not model:
        _show_boot_error()
        raise typer.Exit(code=1)
    if provider != "ollama":
        if provider in OPENAI_COMPATIBLE_PROVIDERS:
            if not os.getenv("OPENAI_API_KEY"):
                _show_boot_error()
                raise typer.Exit(code=1)
            if provider == "other" and not os.getenv("OPENAI_API_BASE"):
                _show_boot_error("other Provider 必须配置 OPENAI_API_BASE。\n")
                raise typer.Exit(code=1)
        elif provider == "anthropic":
            if not os.getenv("ANTHROPIC_API_KEY"):
                _show_boot_error()
                raise typer.Exit(code=1)

    try:
        AgentRunLimits.from_env()
    except RuntimeLimitConfigError as exc:
        _show_boot_error(f"运行预算配置无效：{exc}\n")
        raise typer.Exit(code=1)

    try:
        ContextPolicy.from_env()
    except ContextPolicyError as exc:
        _show_boot_error(f"上下文配置无效：{exc}\n")
        raise typer.Exit(code=1)

    import entry.main as cyberclaw_main
    cyberclaw_main.main()

@app.command("monitor")
def run_monitor():    
        
    try:
        import entry.monitor as cyberclaw_monitor
        cyberclaw_monitor.main()
    except ImportError as e:
        console.print(f"[bold red]启动失败：找不到监视器模块！[/bold red]\n[dim]请确保 monitor.py 和 cli.py 在同一目录下。\n报错信息: {e}[/dim]")

def main():
    app()

if __name__ == "__main__":
    main()
