"""Morning (Green Invoice) (Wave 2 / W2-B): issues the receipt / tax invoice-receipt of each payment and the credit
invoice of each refund through Morning's API. Morning numbers the documents, keeps the PDF and, for a tax invoice
above the threshold, gets the Israel Invoices allocation number itself (connect the Tax Authority once in Morning's
settings).

Credentials, in integrations.env: ``MORNING_API_KEY_ID`` and ``MORNING_API_KEY_SECRET`` (Morning > Settings >
Developer tools > API keys; an API plan is needed). The connector asks for a short-lived token and renews it.

Environment (a setting): **sandbox** by default, Morning's test site, so the first live runs never make real tax
documents; switch to production once a sandbox document looks right.

Fake mode (the default) never calls Morning: documents are numbered from 50001 and their "PDF" is a preview page of
this dashboard, so the whole flow is visible without an account.
"""

from __future__ import annotations

import time
from collections.abc import Mapping

import httpx

from printfarm.agents.integrations.framework import SecretSpec, SettingSpec, SyncContext, SyncError
from printfarm.agents.integrations.invoicing import DocRequest, InvoicingConnector, Issued

BASE = {"production": "https://api.greeninvoice.co.il/api/v1", "sandbox": "https://sandbox.d.greeninvoice.co.il/api/v1"}
DOC_TYPES = {"receipt": 400, "invoice_receipt": 320, "credit": 330}
# Morning's payment types: 1 cash, 2 cheque, 3 credit card, 4 bank transfer, 10 payment app (Bit, PayBox), 11 other
PAYMENT_TYPES = {"cash": 1, "card": 3, "bank_transfer": 4, "bit": 10, "paybox": 10, "unknown": 11}
TIMEOUT_S = 30


def body(req: DocRequest, *, lang: str, email_customer: bool) -> dict:
    """The POST /documents body for a request (pure; tested)."""
    doc_type = DOC_TYPES[req.kind]
    out: dict = {
        "description": req.remarks or f"Payment {req.payment_id}"[:100],
        "type": doc_type,
        "lang": lang,
        "currency": "ILS",
        "vatType": 0,
        "rounding": False,
        "signed": True,
        "client": {"name": req.client_name, "emails": [req.client_email] if email_customer and req.client_email else [],
                   "add": False},
    }
    if doc_type != 400:
        # the lines; prices include VAT (1) for an osek murshe, are VAT exempt (2) for an osek patur
        out["income"] = [{"description": line.description, "quantity": line.quantity,
                          "price": line.unit_price_agorot / 100, "currency": "ILS", "vatType": 2 if req.vat_exempt else 1}
                         for line in req.lines]
    if doc_type in (400, 320):
        out["payment"] = [{"type": PAYMENT_TYPES.get(req.method, 11), "price": req.amount_agorot / 100,
                           "currency": "ILS", "date": req.paid_on}]
    if req.linked_external_id:
        out["linkedDocumentIds"] = [req.linked_external_id]
    return out


def parse(answer: Mapping) -> Issued:
    url = answer.get("url") or {}
    link = url.get("he") or url.get("origin") or url.get("en") or "" if isinstance(url, Mapping) else str(url)
    allocation = next((str(v) for k, v in answer.items() if "allocation" in k.lower() and v), "")
    if not answer.get("id"):
        raise SyncError("Morning answered without a document id")
    return Issued(str(answer["id"]), str(answer.get("number") or ""), link, allocation)


class Morning(InvoicingConnector):
    id = "morning"
    label = "Morning (Green Invoice)"
    secrets = (SecretSpec("MORNING_API_KEY_ID", "API key ID (Morning > Developer tools)"),
               SecretSpec("MORNING_API_KEY_SECRET", "API key secret"))
    settings = (
        SettingSpec("environment", "Morning environment: sandbox (test documents) or production", "sandbox",
                    pattern=r"sandbox|production"),
        SettingSpec("auto_issue", "Issue a document for every payment and refund automatically", True),
        SettingSpec("email_customer", "Morning e-mails the document to the customer", False),
        SettingSpec("lang", "Document language (he or en)", "he", pattern=r"he|en"),
    )

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client
        self._token: tuple[str, float, str] | None = None     # (token, expires at, environment)

    def _settings(self, ctx: SyncContext) -> dict:
        return self.settings_with_defaults(ctx.settings)

    async def check(self, ctx: SyncContext) -> None:
        if ctx.mode == "live":
            await self._access_token(ctx, self._settings(ctx)["environment"])

    async def issue(self, ctx: SyncContext, req: DocRequest, number_hint: int) -> Issued:
        s = self._settings(ctx)
        if ctx.mode != "live":
            if ctx.settings.get("fail"):
                raise SyncError("the test provider is set to refuse documents (settings.fail)")
            return Issued(f"fake-{number_hint}", str(50000 + number_hint), f"/api/v1/documents/{number_hint}/preview")
        env = s["environment"]
        token = await self._access_token(ctx, env)
        r = await (await self._http()).post(f"{BASE[env]}/documents", json=body(req, lang=s["lang"],
                                                                               email_customer=bool(s["email_customer"])),
                                            headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 401:
            self._token = None
            raise SyncError("Morning refused the token (401): check the API key")
        if r.status_code >= 400:
            try:
                msg = str(r.json().get("errorMessage") or r.json().get("message") or "")[:200]
            except ValueError:
                msg = ""
            raise SyncError(f"Morning answered HTTP {r.status_code}" + (f": {msg}" if msg else ""))
        return parse(r.json())

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=TIMEOUT_S)
        return self._client

    async def _access_token(self, ctx: SyncContext, env: str) -> str:
        if self._token and self._token[2] == env and self._token[1] > time.time() + 60:
            return self._token[0]
        r = await (await self._http()).post(f"{BASE[env]}/account/token", json={
            "id": ctx.secrets.get("MORNING_API_KEY_ID", ""), "secret": ctx.secrets.get("MORNING_API_KEY_SECRET", "")})
        if r.status_code != 200:
            raise SyncError(f"Morning did not give a token (HTTP {r.status_code}): check the API key and the environment")
        answer = r.json()
        expires = float(answer.get("expires") or time.time() + 3000)
        self._token = (answer["token"], expires if expires > 1e9 else time.time() + expires, env)
        return self._token[0]
