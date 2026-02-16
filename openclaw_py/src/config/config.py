import os
import json
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field
from dotenv import load_dotenv
import pyjson5

# Load environment variables
load_dotenv()

# --- Auth Config Types ---
class AuthProfileConfig(BaseModel):
    provider: str
    mode: str  # "api_key" | "oauth" | "token"
    email: Optional[str] = None

class AuthConfig(BaseModel):
    profiles: Optional[Dict[str, AuthProfileConfig]] = None
    order: Optional[Dict[str, List[str]]] = None
    cooldowns: Optional[Dict[str, Any]] = None

# --- Gateway Config Types ---
class GatewayControlUiConfig(BaseModel):
    enabled: Optional[bool] = True
    basePath: Optional[str] = None
    root: Optional[str] = None
    allowedOrigins: Optional[List[str]] = None
    allowInsecureAuth: Optional[bool] = False
    dangerouslyDisableDeviceAuth: Optional[bool] = False

class GatewayAuthConfig(BaseModel):
    mode: Optional[str] = "token" # "token" | "password" | "trusted-proxy"
    token: Optional[str] = None
    password: Optional[str] = None
    allowTailscale: Optional[bool] = None
    rateLimit: Optional[Dict[str, Any]] = None
    trustedProxy: Optional[Dict[str, Any]] = None

class GatewayHttpConfig(BaseModel):
    endpoints: Optional[Dict[str, Any]] = None

class GatewayConfig(BaseModel):
    port: int = 18789
    mode: Optional[str] = "local" # "local" | "remote"
    bind: Optional[str] = "loopback" # "auto" | "lan" | "loopback" | "custom" | "tailnet"
    customBindHost: Optional[str] = None
    controlUi: Optional[GatewayControlUiConfig] = Field(default_factory=GatewayControlUiConfig)
    auth: Optional[GatewayAuthConfig] = Field(default_factory=GatewayAuthConfig)
    tailscale: Optional[Dict[str, Any]] = None
    remote: Optional[Dict[str, Any]] = None
    reload: Optional[Dict[str, Any]] = None
    tls: Optional[Dict[str, Any]] = None
    http: Optional[GatewayHttpConfig] = None
    nodes: Optional[Dict[str, Any]] = None
    trustedProxies: Optional[List[str]] = None
    tools: Optional[Dict[str, Any]] = None

# --- Logging Config Types ---
class LoggingConfig(BaseModel):
    level: Optional[str] = "info" # "silent" | "fatal" | "error" | "warn" | "info" | "debug" | "trace"
    file: Optional[str] = None
    consoleLevel: Optional[str] = "info"
    consoleStyle: Optional[str] = "pretty" # "pretty" | "compact" | "json"
    redactSensitive: Optional[str] = "tools" # "off" | "tools"
    redactPatterns: Optional[List[str]] = None

# --- Root Config Type ---
class OpenClawConfig(BaseModel):
    auth: Optional[AuthConfig] = None
    gateway: Optional[GatewayConfig] = Field(default_factory=GatewayConfig)
    logging: Optional[LoggingConfig] = Field(default_factory=LoggingConfig)
    # Add other sections as needed (agents, browser, etc.)

def load_config() -> OpenClawConfig:
    """
    Loads configuration from environment variables and config file.
    Mirrors logic from src/config/io.ts
    """
    config_path = os.getenv("OPENCLAW_CONFIG_PATH")

    # Default config path logic (simplified for Python version)
    if not config_path:
        home_dir = os.path.expanduser("~")
        config_path = os.path.join(home_dir, ".openclaw", "openclaw.json")

    config_data = {}

    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                # Use pyjson5 to support JSON5 (comments, trailing commas, etc.)
                config_data = pyjson5.load(f)
        except Exception as e:
            print(f"Error loading config file from {config_path}: {e}")
            # Fallback or exit? For now, we continue with empty/env vars.

    # TODO: Implement full deep merge and environment variable overrides here.
    # For now, Pydantic handles defaults.

    try:
        config = OpenClawConfig(**config_data)
        return config
    except Exception as e:
        print(f"Config validation error: {e}")
        return OpenClawConfig()
