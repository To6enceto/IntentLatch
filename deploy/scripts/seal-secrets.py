#!/usr/bin/env python3
"""Generate event credentials in memory and persist only strict-scope ciphertext."""
import secrets

from common import KUBE, SEALED, atomic_json, run, seal


def main():
    if SEALED.exists():
        print(f"Keeping existing ciphertext in {SEALED}. No credentials rotated.")
        return
    existing = run([*KUBE, "-n", "intentlatch-data", "get", "secret", "postgres-auth", "--ignore-not-found=true", "-o", "name"], capture=True).stdout.strip()
    if existing:
        raise SystemExit("postgres-auth already exists. Restore the matching sealed-values.json instead of changing an initialized database password.")
    db_password = secrets.token_urlsafe(32)
    valkey_password = secrets.token_urlsafe(32)
    definitions = {
        "postgres": ("intentlatch-data", "postgres-auth", {
            "postgres-password": secrets.token_urlsafe(32), "app-password": db_password}),
        "gateway": ("intentlatch-system", "gateway-auth", {
            "INTENTLATCH_DATABASE_URL": f"postgresql://intentlatch:{db_password}@postgres.intentlatch-data.svc.cluster.local:5432/intentlatch",
            "INTENTLATCH_TOKEN_SIGNING_KEY": secrets.token_urlsafe(32),
            "INTENTLATCH_ADMIN_API_KEY": secrets.token_urlsafe(32)}),
        "valkey": ("intentlatch-data", "valkey-auth", {"password": valkey_password}),
        "valkeyUrl": ("intentlatch-system", "gateway-valkey", {
            "url": f"redis://:{valkey_password}@valkey.intentlatch-data.svc.cluster.local:6379/0"}),
        "webui": ("agents", "webui-auth", {"WEBUI_SECRET_KEY": secrets.token_urlsafe(32)}),
        # Invalid by design until smoke.py --bootstrap-only seals a real employee.
        "employee": ("agents", "webui-employee", {"OPENAI_API_KEY": secrets.token_urlsafe(32)}),
        "grafana": ("observability", "grafana-auth", {
            "admin-user": "admin", "admin-password": secrets.token_urlsafe(32)}),
    }
    encrypted = {key: seal(namespace, name, values) for key, (namespace, name, values) in definitions.items()}
    atomic_json(SEALED, {"sealedSecrets": encrypted})
    print(f"Saved encrypted values to {SEALED}. No secret values were printed or written to disk.")


if __name__ == "__main__":
    main()
