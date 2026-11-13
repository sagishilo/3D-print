from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    nats_url: str = "nats://nats:4222"
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


settings = Settings()
