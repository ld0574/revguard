"""Provision one read-only shared dashboard from a short-lived Docker helper.

Mount the Grafana password and public-token output directory in this helper only;
the API service never receives a Grafana admin password.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def main():
    directory = Path(os.getenv("REVGUARD_OBSERVABILITY_SECRETS", "/run/observability"))
    password = (directory / "grafana-password").read_text().strip()
    base = os.getenv("REVGUARD_GRAFANA_ADMIN_URL", "http://grafana:3000/grafana").rstrip("/")
    parsed = urlsplit(base)
    if (parsed.scheme != "http" or parsed.hostname not in {"grafana", "revguard-grafana-preview", "127.0.0.1"}
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Only the local Grafana service is supported")
    headers = {
        "Authorization": "Basic " + base64.b64encode(("admin:" + password).encode()).decode(),
        "Content-Type": "application/json",
    }
    endpoint = base + "/api/dashboards/uid/revguard-operations/public-dashboards"
    for attempt in range(30):
        try:
            request = urllib.request.Request(endpoint, headers=headers)
            try:
                # Fixed Docker-internal Grafana endpoint, never browser-supplied.
                with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310
                    dashboard = json.load(response)
                method = "PATCH"
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
                dashboard = {}
                method = "POST"
            if not dashboard.get("isEnabled"):
                mutation_endpoint = endpoint + "/" + dashboard["uid"] if method == "PATCH" else endpoint
                request = urllib.request.Request(mutation_endpoint, headers=headers, method=method,
                    data=json.dumps({"isEnabled": True, "annotationsEnabled": False,
                                     "timeSelectionEnabled": True, "share": "public"}).encode())
                with urllib.request.urlopen(request, timeout=10) as response:  # nosec B310
                    dashboard = json.load(response)
            token = directory / "grafana-public-token"
            token.write_text(dashboard["accessToken"])
            token.chmod(0o644)
            print("Grafana selected-dashboard sharing enabled; no admin credential exposed")
            return
        except (urllib.error.URLError, TimeoutError):
            if attempt == 29:
                raise SystemExit("Grafana dashboard provisioning did not become ready") from None
            time.sleep(2)


if __name__ == "__main__":
    main()
