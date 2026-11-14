from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    nats_url: str = "nats://nats:4222"
    nats_monitor_url: str = "http://nats:8222"   # NATS monitoring API (/varz, /connz, /jsz) for the System screen
    go2rtc_url: str = "http://go2rtc:1984"
    jwt_secret: str
    admin_username: str = "admin"
    admin_password_hash: str          # argon2id hash, generate with: python -m app.hashpw
    cookie_secure: bool = True        # keep True behind Caddy HTTPS
    access_ttl_s: int = 15 * 60
    refresh_ttl_s: int = 7 * 24 * 3600
    command_timeout_s: float = 5.0
    login_max_failures: int = 5
    login_lockout_s: int = 15 * 60
    # Browser origins allowed to open /ws: https://<each host in SITE_HOSTS>, plus EXTRA_ORIGINS (comma separated),
    # e.g. "http://localhost:5173" for the Vite dev server.
    site_hosts: str = "localhost"
    extra_origins: str = ""

    def allowed_origins(self) -> set[str]:
        hosts = {f"https://{h.strip()}" for h in self.site_hosts.split(",") if h.strip()}
        return hosts | {o.strip().rstrip("/") for o in self.extra_origins.split(",") if o.strip()}


settings = Settings()
