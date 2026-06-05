# =========================
from types import SimpleNamespace

# --- COUNTRY LOCK helpers (CO/EC) ---

# PATCH: MX_META_CAPI_PER_HOST_V1_20260220
def _mx_country_from_host(host: str) -> str:
    h = (host or "").lower().strip()
    if "colombia.maxtourus.com.br" in h or h.startswith("colombia"):
        return "CO"
    if "ec.maxlien.shop" in h or h.startswith("ec."):
        return "EC"
    return ""

def _mx_env_country(country: str, key: str, default: str = "") -> str:
    import os
    c = (country or "").upper().strip()
    if c:
        v = (os.getenv(f"{key}_{c}", "") or "").strip()
        if v:
            return v
    return (os.getenv(key, default) or "").strip()
# END PATCH: MX_META_CAPI_PER_HOST_V1_20260220


_DB_BY_COUNTRY = {
    "EC": "/opt/maxlien-mvp/leads_ec.sqlite3",
    "CO": "/opt/maxlien-mvp/leads_co.sqlite3",
}

def _digits(v):
    import re as _re
    return _re.sub(r"\D+", "", str(v or ""))

def _infer_country_from_phone(raw_phone: str, host_country: str = "") -> str:
    p = _digits(raw_phone)
    hc = (host_country or "").strip().upper()
    if p.startswith("59357"):
        p2 = p[3:]
        if p2.startswith("57"):
            return "CO"
    if p.startswith("57"):
        return "CO"
    if p.startswith("593"):
        return "EC"
    if len(p) == 10:
        if p.startswith("09"):
            return "EC"
        if p.startswith("3"):
            return "CO"
        if hc in ("EC", "CO"):
            return hc
    if len(p) == 9:
        if p.startswith("9"):
            return "EC"
        if hc in ("EC", "CO"):
            return hc
    return hc or ""
# --- /COUNTRY LOCK helpers ---



def _mvp_wa_number_for_host(host: str) -> str:
    h = (host or "").lower().strip()
    if "co.maxlien.shop" in h or "colombia.maxtourus.com.br" in h or h.startswith("colombia"):
        return (globals().get("WA_ME_NUMBER_CO") or globals().get("WA_ME_NUMBER") or "").strip()
    if "ec.maxlien.shop" in h or h.startswith("ec."):
        return (globals().get("WA_ME_NUMBER_EC") or globals().get("WA_ME_NUMBER") or "").strip()
    return (globals().get("WA_ME_NUMBER") or "").strip()
PRICE_TABLE = {
    "EC": {"1": 39.99, "2": 69.99, "3": 95.99, "6": 167.99},
    "CO": {"1": 170000, "2": 300000, "3": 350000, "6": 584000},
}

def _price_for(country, qty):
    c = (country or "").strip().upper()
    q = str(qty or "").strip()
    return PRICE_TABLE.get(c, {}).get(q)
# === PRICE_BLOCK_END ===

# =========================
# STDLIB (Python padrão)
# =========================
import os
import re
import csv
import sqlite3


# PATCH: MX_LEAD_CAPI_DBMARK_V4_6_20260221_003736_DBHELP
def _mx_mark_lead_capi(cur, lead_id: int, http_status: int, fbtrace_id: str):
    """Marca telemetria do Lead CAPI no mesmo DB do lead (transação atual)."""
    try:
        ts = now_iso_utc()
        st = int(http_status or 0)
        fb = (fbtrace_id or "").strip()
        sent = 1 if st == 200 else 0
        cur.execute(
            "UPDATE leads SET lead_capi_sent=?, lead_capi_last_ts=?, lead_capi_last_status=?, lead_capi_fbtrace_id=? WHERE id=?",
            (sent, ts, st, fb, int(lead_id)),
        )
    except Exception:
        pass
# END PATCH: MX_LEAD_CAPI_DBMARK_V4_6_20260221_003736_DBHELP
import time
from io import StringIO
from datetime import datetime, timezone, timedelta


# =========================================
# FINAL90: ORIGEM + CAPI (EC/CO) + DEDUPE
# =========================================
### MAXLIEN_FINAL90_HELPERS ###
import os, json, hashlib, urllib.request, urllib.error

PIXEL_EC_DEFAULT = "1575809370185036"
PIXEL_CO_DEFAULT = "1463862632004765"

# PATCH: MX_FINAL90_HOSTMAP_CLEAN_CO_V4_ALLHELPERS_20260221
def _mx_final90_is_co_host(host: str) -> bool:
    """Resolver único CO por host — SEM CONTAMINAÇÃO."""
    h = (host or "").strip().lower()
    # X-Forwarded-Host pode vir "a,b"
    if "," in h:
        h = h.split(",", 1)[0].strip()
    # remove :porta
    if ":" in h:
        h = h.split(":", 1)[0].strip()

    return (
        h == "co.maxlien.shop" or h.endswith(".co.maxlien.shop") or
        h == "colombia.maxtourus.com.br" or h.endswith(".colombia.maxtourus.com.br") or
        h.startswith("co.") or h.startswith("colombia.")
    )
# END PATCH: MX_FINAL90_HOSTMAP_CLEAN_CO_V4_ALLHELPERS_20260221


def _final90_pixel_for_host(host: str) -> str:
    h = (host or "").lower()
    px_ec = os.getenv("MVP_FB_PIXEL_ID_EC", PIXEL_EC_DEFAULT).strip()
    px_co = os.getenv("MVP_FB_PIXEL_ID_CO", PIXEL_CO_DEFAULT).strip()
    return px_co if _mx_final90_is_co_host(h) else px_ec

def _final90_get_ip(req):
    try:
        xff = (req.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        return xff or (getattr(req, "remote_addr", "") or "")
    except Exception:
        return ""

def _final90_sha256(s: str) -> str:
    s = (s or "").strip().lower()
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def _final90_digits(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())

def _final90_capi_send_lead(host: str, data: dict, ua: str, ip: str):
    """
    Dispara Lead via Conversions API com event_id vindo do client (dedupe).

    Requer:
      MVP_FB_ACCESS_TOKEN_EC / MVP_FB_ACCESS_TOKEN_CO

    Opcional:
      MVP_FB_TEST_EVENT_CODE (apenas para teste no Events Manager)
      MVP_FB_PIXEL_ID_EC / MVP_FB_PIXEL_ID_CO (override)
    """
    # token + pixel por host (EC vs CO) — sem mistura
    h = (host or "").strip().lower()
    h = h.split(",")[0].split(":")[0].strip()

    # PATCH: MX_FINAL90_HOSTMAP_CLEAN_CO_V2_20260220__20260221_004357
    # Resolver CO por host — SEM CONTAMINAÇÃO
    is_co = (
        h == "co.maxlien.shop" or h.endswith(".co.maxlien.shop") or
        h == "colombia.maxtourus.com.br" or h.endswith(".colombia.maxtourus.com.br") or
        h.startswith("co.") or h.startswith("colombia.")
    )
    if is_co:
        token = (os.getenv("MVP_FB_ACCESS_TOKEN_CO") or "").strip()
        pixel_id = (os.getenv("MVP_FB_PIXEL_ID_CO") or PIXEL_CO_DEFAULT).strip()
    else:
        token = (os.getenv("MVP_FB_ACCESS_TOKEN") or os.getenv("MVP_FB_ACCESS_TOKEN_EC") or "").strip()
        pixel_id = (os.getenv("MVP_FB_PIXEL_ID") or PIXEL_EC_DEFAULT).strip()

    if not token:
        return False, "missing_token"

    test_code = (os.getenv("MVP_FB_TEST_EVENT_CODE") or "").strip()

    event_id = (data.get("event_id") or "").strip()
    event_source_url = (data.get("event_source_url") or "").strip()

    # user_data recomendado
    phone_norm = _final90_digits(str(data.get("phone") or ""))
    user_data = {
        "client_ip_address": ip or "",
        "client_user_agent": ua or "",
    }

    # fbp/fbc ajudam MUITO no match
    fbp = (data.get("fbp") or "").strip()
    fbc = (data.get("fbc") or "").strip()
    if fbp:
        user_data["fbp"] = fbp
    if fbc:
        user_data["fbc"] = fbc

    # hash phone (se existir)
    if phone_norm:
        user_data["ph"] = [_final90_sha256(phone_norm)]

    payload = {
        "data": [{
            "event_name": "Lead",
            "event_time": int(time.time()),
            "action_source": "website",
            "event_id": event_id or None,
            "event_source_url": event_source_url or None,
            "user_data": user_data,
        }]
    }

    if test_code:
        payload["test_event_code"] = test_code

    url = f"https://graph.facebook.com/v20.0/{pixel_id}/events?access_token={token}"

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            # FINAL90_META_URLLIB_HTTPLOG_V1
            _body = resp.read()
            try:
                _st = getattr(resp, "status", None)
                try:
                    _tx = (_body or b"")[:1400].decode("utf-8", errors="replace")
                except Exception:
                    _tx = str(_body or b"")[:1400]

                app.logger.error(
                    "FINAL90_META_URLLIB_HTTP status=%s host=%s pixel=%s body=%s"
                    % (_st, host, pixel_id, _tx)
                )

                print(
                    "FINAL90_META_URLLIB_PRINT status=%s host=%s pixel=%s"
                    % (_st, host, pixel_id),
                    flush=True
                )
            except Exception:
                pass

        return True, "sent"

    except Exception as e:
        # MX_META_HTTPERROR_BODYLOG_V5_20260224
        # MX_META_HTTPERROR_BODYLOG_V5_1_20260224
        try:
            _etype = str(type(e))
            _has_read = callable(getattr(e, 'read', None))
            print('FINAL90_META_URLLIB_ERRTYPE etype=%s has_read=%s' % (_etype, _has_read), flush=True)
            if _has_read:
                try:
                    _raw = e.read()
                except Exception:
                    _raw = b''
                try:
                    _tx = (_raw or b'')[:1600].decode('utf-8','ignore')
                except Exception:
                    _tx = str(_raw or b'')[:1600]
                print('FINAL90_META_URLLIB_BODYLEN n=%s' % (len(_raw or b'')), flush=True)
                print('FINAL90_META_URLLIB_BODY body=%s' % (_tx,), flush=True)
        except Exception:
            pass
        if isinstance(e, urllib.error.HTTPError):
            try:
                _b = e.read().decode('utf-8','ignore')
            except Exception:
                _b = '[unreadable]'
            try:
                print('FINAL90_META_URLLIB_EXCEPT host=%s pixel=%s err=%s body=%s' % (host, pixel_id, str(e), _b), flush=True)
            except Exception:
                pass
        try:
            print(
                "FINAL90_META_URLLIB_EXCEPT src=OLD host=%s pixel=%s err=%s"
                % (host, pixel_id, str(e)),
                flush=True
            )
        except Exception:
            pass
        return False, f"error:{e}"


# PATCH: MX_LEAD_CAPI_DBMARK_V4_6_20260221_003736
def _final90_capi_send_lead_rich(host: str, data: dict, ua: str, ip: str):
    """Lead CAPI (RICH): retorna (ok:bool, msg:str, http_status:int, fbtrace_id:str)."""
    h = (host or "").strip().lower()
    h = h.split(",")[0].split(":")[0].strip()

    if _mx_final90_is_co_host(h):
        token = (os.getenv("MVP_FB_ACCESS_TOKEN_CO") or "").strip()
        pixel_id = (os.getenv("MVP_FB_PIXEL_ID_CO") or PIXEL_CO_DEFAULT).strip()
    else:
        token = (os.getenv("MVP_FB_ACCESS_TOKEN") or os.getenv("MVP_FB_ACCESS_TOKEN_EC") or "").strip()
        pixel_id = (os.getenv("MVP_FB_PIXEL_ID") or PIXEL_EC_DEFAULT).strip()

    if not token:
        return False, "missing_token", 0, ""

    test_code = (os.getenv("MVP_FB_TEST_EVENT_CODE") or "").strip()

    event_id = (data.get("event_id") or "").strip()
    event_source_url = (data.get("event_source_url") or "").strip()

    phone_norm = _final90_digits(str(data.get("phone") or ""))
    user_data = {
        "client_ip_address": ip or "",
        "client_user_agent": ua or "",
    }

    fbp = (data.get("fbp") or "").strip()
    fbc = (data.get("fbc") or "").strip()
    if fbp: user_data["fbp"] = fbp
    if fbc: user_data["fbc"] = fbc
    if phone_norm: user_data["ph"] = [_final90_sha256(phone_norm)]

    payload = {
        "data": [{
            "event_name": "Lead",
            "event_time": int(time.time()),
            "action_source": "website",
            "event_id": event_id or None,
            "event_source_url": event_source_url or None,
            "user_data": user_data,
        }]
    }
    if test_code:
        payload["test_event_code"] = test_code

    url = f"https://graph.facebook.com/v20.0/{pixel_id}/events?access_token={token}"

    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            _body = resp.read()
            _st = int(getattr(resp, "status", 0) or 0)
            _fb = ""
            try:
                _j = json.loads((_body or b"").decode("utf-8", errors="replace"))
                _fb = str(_j.get("fbtrace_id") or "")
            except Exception:
                _fb = ""
            # mantém os logs existentes (igual ao helper antigo)
            try:
                _tx = (_body or b"")[:1400].decode("utf-8", errors="replace")
            except Exception:
                _tx = str(_body or b"")[:1400]
            try:
                app.logger.error("FINAL90_META_URLLIB_HTTP status=%s host=%s pixel=%s body=%s" % (_st, host, pixel_id, _tx))
                print("FINAL90_META_URLLIB_PRINT status=%s host=%s pixel=%s" % (_st, host, pixel_id), flush=True)
            except Exception:
                pass
        return True, "sent", _st, _fb

    except Exception as e:
        try:
            # MX_META_HTTPERROR_BODYLOG_RICH_V1_20260224
            try:
                _etype = str(type(e))
                _has_read = callable(getattr(e, 'read', None))
                print('FINAL90_META_RICH_ERRTYPE etype=%s has_read=%s host=%s pixel=%s' % (_etype, _has_read, host, pixel_id), flush=True)
                if _has_read:
                    try:
                        _raw = e.read()
                    except Exception:
                        _raw = b''
                    try:
                        _tx = (_raw or b'')[:2000].decode('utf-8','ignore')
                    except Exception:
                        _tx = str(_raw or b'')[:2000]
                    print('FINAL90_META_RICH_BODYLEN n=%s' % (len(_raw or b'')), flush=True)
                    print('FINAL90_META_RICH_BODY body=%s' % (_tx,), flush=True)
            except Exception:
                pass
            print("FINAL90_META_URLLIB_EXCEPT src=RICH host=%s pixel=%s err=%s" % (host, pixel_id, str(e)), flush=True)
        except Exception:
            pass
        return False, f"error:{e}", 0, ""
# END PATCH: MX_LEAD_CAPI_DBMARK_V4_6_20260221_003736

# =========================
# THIRD-PARTY
# =========================
import requests

# from flask import Flask, request, session, redirect, url_for, render_template, render_template_string
from flask import Flask, jsonify, redirect, render_template, render_template_string, request, session, url_for, flash, has_request_context, make_response, send_from_directory

from werkzeug.security import check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

# =========================
# TELEGRAM ALERTS
# =========================

TG_BOT_TOKEN = (os.getenv("MVP_TG_BOT_TOKEN") or "").strip()
TG_CHAT_ID = (os.getenv("MVP_TG_CHAT_ID") or "").strip()

def send_telegram_alert(text: str) -> bool:
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        app.logger.warning("Telegram não configurado (token/chat_id ausente)")
        return False

    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": TG_CHAT_ID, "text": text}, timeout=10)


        # FINAL90_CAPI_HTTPLOG_V1
        try:
            sc = getattr(r, "status_code", None)
            tx = getattr(r, "text", "") or ""
            app.logger.info("FINAL90_CAPI_HTTP status=%s body=%s" % (sc, tx[:220]))
        except Exception:
            pass
        if r.status_code != 200:
            app.logger.error(f"Telegram erro {r.status_code}: {r.text[:300]}")
            return False

        app.logger.info("Telegram OK: mensagem enviada")
        return True
    except Exception as e:
        app.logger.exception(f"Telegram exception: {e}")
        return False

    r = requests.post(url, headers=headers, json=payload, timeout=20)
    data = r.json() if r.content else {}
    if r.status_code >= 300:
        raise RuntimeError(f"WA HTTP {r.status_code}: {data}")
    return data

# ============================
# WHATSAPP CLOUD API (TREINO)
# ============================
WA_TOKEN = (os.getenv("MVP_WA_TOKEN") or "").strip()
WA_PHONE_NUMBER_ID = (os.getenv("MVP_WA_PHONE_NUMBER_ID") or "").strip()
WA_TEMPLATE_NAME = (os.getenv("MVP_WA_TEMPLATE_NAME") or "").strip()
WA_TEMPLATE_LANG = (os.getenv("MVP_WA_TEMPLATE_LANG") or "es").strip()

# ===============================
# WA.ME (NO API) - ANTI-SPAM
# ===============================
import urllib.parse
from datetime import datetime, timezone



def build_wa_text(country: str, name: str, city: str) -> str:
    c = (country or "EC").strip().upper()
    n = (name or "").strip() or "Hola"
    cty = (city or "").strip()

    if c == "CO":
        base = f"Hola {n}. Soy del equipo de la Doctora Maria Fernanda. ✅ Tu pedido ya fue registrado y necesito confirmarlo hoy para garantizar el envío en Colombia. ¿Me dices tu ciudad y barrio?"
        extra = "Guarda este número para darte prioridad y enviarte la confirmación."
        return base + " " + extra
    else:
        base = f"¡Hola {n}! ✅ Para confirmar tu pedido y coordinar la entrega en Ecuador, ¿me confirmas tu ciudad y una referencia?"
        extra = "Guarda este número para atención prioritaria y confirmación rápida."
        return base + " " + extra


WA_ME_NUMBER = (os.getenv("MVP_WA_ME_NUMBER") or os.getenv("WA_ME_NUMBER") or "").strip()  # fallback (sem request)
WA_ME_NUMBER_CO = (os.getenv("MVP_WA_ME_NUMBER_CO") or os.getenv("WA_ME_NUMBER_CO") or "").strip()
WA_ME_NUMBER_EC = (os.getenv("MVP_WA_ME_NUMBER_EC") or os.getenv("WA_ME_NUMBER_EC") or "").strip()
WA_COOLDOWN_SECONDS = int(os.getenv("MVP_WA_COOLDOWN_SECONDS") or "14400")  # 4h
WA_MAX_PER_DAY = int(os.getenv("MVP_WA_MAX_PER_DAY") or "1")  # 1 por dia
WA_SEND_FIRST_MESSAGE = (os.getenv("MVP_WA_SEND_FIRST_MESSAGE") or "1").strip().lower() in ("1", "true", "yes", "on", "sim")

# 30 mensajes (Español colombiano) - se elige uno automáticamente
WA_MSG_CO = [
  "Hola {name}, vi tu solicitud. ¿En qué ciudad estás para ayudarte con la entrega?",
  "¡Hola {name}! Ya me apareció tu registro. ¿Me confirmas tu ciudad, por favor?",
  "Hola {name}, gracias por escribir. ¿Ciudad y si prefieres domicilio o punto de entrega?",
  "¡Qué más, {name}! Vi tu pedido. ¿Me dices tu ciudad para seguir?",
  "Hola {name}, te atiendo por aquí. ¿En qué ciudad estás ubicado(a)?",
  "Hola {name}, ya tengo tu solicitud. ¿Me confirmas ciudad y barrio?",
  "¡Hola {name}! ¿Me confirmas tu ciudad para revisar disponibilidad de entrega?",
  "Hola {name}, listo para ayudarte. ¿Me dices tu ciudad, por favor?",
  "¡Hola {name}! ¿Prefieres entrega a domicilio o recogida? Dime tu ciudad.",
  "Hola {name}, recibimos tu solicitud. ¿En qué ciudad estás para continuar?",
  "¡Hola {name}! Te hablo por tu registro. ¿Me confirmas tu ciudad?",
  "Hola {name}, ¿cómo vas? Vi tu solicitud. ¿En qué ciudad estás?",
  "Hola {name}, te colaboro con gusto. ¿Cuál es tu ciudad?",
  "¡Hola {name}! Para seguir con tu pedido, ¿me dices tu ciudad?",
  "Hola {name}, ya estoy pendiente de ti. ¿Ciudad y referencia de entrega?",
  "Hola {name}, vi tu formulario. ¿Me confirmas tu ciudad para avanzar?",
  "¡Hola {name}! ¿Qué ciudad es para coordinar la entrega?",
  "Hola {name}, gracias por el interés. ¿En qué ciudad estás?",
  "Hola {name}, te explico todo por acá. ¿Me confirmas tu ciudad?",
  "¡Hola {name}! ¿Me dices tu ciudad y si es para entrega o retiro?",
  "Hola {name}, tu solicitud llegó bien. ¿En qué ciudad estás?",
  "Hola {name}, ¿me confirmas tu ciudad para darte la info exacta?",
  "Hola {name}, te atiendo ahora. ¿Qué ciudad es?",
  "Hola {name}, ¿en qué ciudad te encuentras para coordinar?",
  "Hola {name}, ¿me confirmas ciudad para continuar con el proceso?",
  "Hola {name}, ¿me dices tu ciudad para revisar el envío?",
  "Hola {name}, ya vi tu registro. ¿Qué ciudad es para organizar la entrega?",
  "Hola {name}, ¿me confirmas tu ciudad y tu barrio?",
  "Hola {name}, listo. ¿Cuál es tu ciudad para seguir con tu solicitud?",
  "Hola {name}, ¿me confirmas tu ciudad para ayudarte de una?"
]

def _today_yyyymmdd_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")

def wa_offer_ensure_table(conn):
    cur = conn.cursor()
    cur.execute("""
      CREATE TABLE IF NOT EXISTS wa_offer (
        phone TEXT PRIMARY KEY,
        last_ts INTEGER NOT NULL,
        day_yyyymmdd TEXT NOT NULL,
        day_count INTEGER NOT NULL
      )
    """)
    conn.commit()

def wa_offer_check_and_mark(phone_norm: str, now_ts: int) -> tuple[bool, str]:
    """
    Regras rígidas:
      - se phone vazio -> bloqueia
      - se já ofereceu e ainda está em cooldown -> bloqueia
      - se já ofereceu >= MAX_PER_DAY no mesmo dia -> bloqueia
      - se passou cooldown e ainda não estourou MAX_PER_DAY -> libera e marca
    """
    if not phone_norm:
        return (False, "no_phone")

    conn = db_conn()
    try:
        wa_offer_ensure_table(conn)
        cur = conn.cursor()
        cur.execute("SELECT last_ts, day_yyyymmdd, day_count FROM wa_offer WHERE phone=?", (phone_norm,))
        row = cur.fetchone()

        today = _today_yyyymmdd_utc()

        if not row:
            cur.execute("INSERT INTO wa_offer(phone,last_ts,day_yyyymmdd,day_count) VALUES(?,?,?,?)",
                        (phone_norm, now_ts, today, 1))
            conn.commit()
            return (True, "first_time")

        last_ts, day_yyyymmdd, day_count = int(row[0]), str(row[1]), int(row[2])

        # reset diário
        if day_yyyymmdd != today:
            day_yyyymmdd = today
            day_count = 0

        # cooldown
        if now_ts - last_ts < WA_COOLDOWN_SECONDS:
            return (False, "cooldown")

        # limite diário
        if day_count >= WA_MAX_PER_DAY:
            return (False, "max_per_day")

        # libera e marca
        day_count += 1
        cur.execute("UPDATE wa_offer SET last_ts=?, day_yyyymmdd=?, day_count=? WHERE phone=?",
                    (now_ts, day_yyyymmdd, day_count, phone_norm))
        conn.commit()
        return (True, "ok")

    finally:
        conn.close()

def wa_me_url(phone_business_e164: str, text: str) -> str:
    # wa.me exige número sem '+' e texto URL-encoded
    phone_business_e164 = re.sub(r"[^0-9]", "", phone_business_e164 or "")
    q = urllib.parse.quote(text or "", safe="")
    return f"https://wa.me/{phone_business_e164}?text={q}"

def wa_app_intent_url(phone_business_e164: str, text: str) -> str:
    phone_business_e164 = re.sub(r"[^0-9]", "", phone_business_e164 or "")
    q = urllib.parse.quote(text or "", safe="")
    return f"intent://send?phone={phone_business_e164}&text={q}#Intent;scheme=whatsapp;package=com.whatsapp;end"

def _wa_router_numbers_for_country(country: str, host: str = "") -> list[str]:
    c = (country or "").strip().upper()
    env_key = f"MVP_WA_ROUTER_NUMBERS_{c}" if c else "MVP_WA_ROUTER_NUMBERS"
    raw = (os.getenv(env_key) or os.getenv("MVP_WA_ROUTER_NUMBERS") or "").strip()
    if not raw and c == "EC":
        raw = "553183002800"
    if not raw:
        single = _mvp_wa_number_for_host(host) or WA_ME_NUMBER
        raw = single or ""
    nums = []
    seen = set()
    for part in re.split(r"[,;\\s]+", raw):
        n = re.sub(r"[^0-9]", "", part or "")
        if n and n not in seen:
            seen.add(n)
            nums.append(n)
    return nums

def _wa_router_open_numbers(country: str, host: str = "") -> list[str]:
    return _wa_router_numbers_for_country(country, host)

def pick_wa_business_number(country: str, host: str, lead_id: int | None = None) -> str:
    nums = _wa_router_open_numbers(country, host)
    if not nums:
        return (_mvp_wa_number_for_host(host) or WA_ME_NUMBER or "").strip()
    seed = int(lead_id or time.time())
    return nums[seed % len(nums)]

def pick_wa_msg_variant(lead_id: int, name: str) -> str:
    # determinístico: evita repetir ao recarregar (usa lead_id)
    idx = int(lead_id or 0) % len(WA_MSG_CO)
    base = WA_MSG_CO[idx]
    nm = (name or "").strip() or "amigo"
    return base.format(name=nm)

def wa_normalize_e164(raw: str) -> str:
    s = (raw or "").strip()
    s = re.sub(r"[^\d+]", "", s)
    if s and not s.startswith("+"):
        s = "+" + s
    return s

def wa_log(db_path: str, lead_id: int, to_phone: str, message_type: str,
           template_name: str = "", wa_message_id: str = "", status: str = "", error: str = ""):
    # SAFE: log de WhatsApp nunca pode derrubar captura de lead.
    # Se db_path vier None/vazio, apenas ignora.
    if not db_path:
        return
    conn = None
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS whatsapp_log (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              created_at TEXT DEFAULT (datetime(now)),
              lead_id INTEGER,
              to_phone TEXT,
              message_type TEXT,
              template_name TEXT,
              wa_message_id TEXT,
              status TEXT,
              error TEXT
            )
        """)
        cur.execute("""
            INSERT INTO whatsapp_log (lead_id, to_phone, message_type, template_name, wa_message_id, status, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (lead_id, to_phone, message_type, template_name, wa_message_id, status, (error or "")[:2000]))
        conn.commit()
    except Exception:
        # nunca levantar excecao aqui
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass

def wa_send_first_message_template(to_phone_e164: str) -> dict:
    # SAFE: nunca derrubar /api/lead por falta de WA Cloud.
    # Se nao estiver configurado, apenas retorna skipped.
    if not WA_TOKEN or not WA_PHONE_NUMBER_ID:
        return {"ok": False, "skipped": True, "reason": "wa_not_configured"}
    if not WA_TEMPLATE_NAME:
        return {"ok": False, "skipped": True, "reason": "wa_template_not_configured"}

    url = f"https://graph.facebook.com/v20.0/{WA_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WA_TOKEN}", "Content-Type": "application/json"}

    payload = {
        "messaging_product": "whatsapp",
        "to": (to_phone_e164 or "").replace("+", ""),
        "type": "template",
        "template": {
            "name": WA_TEMPLATE_NAME,
            "language": {"code": WA_TEMPLATE_LANG},
        },
    }

    try:
        r = requests.post(url, headers=headers, json=payload, timeout=20)
        data = r.json() if getattr(r, "content", None) else {}
        if r.status_code >= 300:
            return {"ok": False, "error": f"wa_http_{r.status_code}", "data": data}
        return {"ok": True, "data": data}
    except Exception as e:
        return {"ok": False, "error": "wa_exception", "detail": str(e)}

def _db_path_by_host(host: str) -> str:
    """
    Seleciona DB por domínio para evitar mistura EC/CO.
    MVP_DB_PATH (se setado) tem prioridade e sobrescreve tudo.
    """
    import os
    override = (os.getenv("MVP_DB_PATH") or "").strip()
    if override:
        return override

    h = (host or "").lower().split(":")[0].strip()
    base = "/opt/maxlien-mvp"
    if _mx_final90_is_co_host(h):
        return f"{base}/leads_co.sqlite3"
    return f"{base}/leads_ec.sqlite3"

def get_db_path() -> str:
    try:
        return _db_path_by_host(getattr(request, "host", ""))
    except Exception:
        return "/opt/maxlien-mvp/leads_ec.sqlite3"

DB_PATH = None  # definido dinamicamente por host em get_db_path()
ADMIN_USER = os.getenv("MVP_ADMIN_USER", "admin").strip()
ADMIN_PASS_HASH = (os.getenv("MVP_ADMIN_PASS_HASH") or "").strip()
SECRET_KEY = os.getenv("MVP_SECRET_KEY", "dev-secret-change-me").strip()
STATUSES = ["novo","atendendo","comprar_depois","confirmado","pedido_enviado","entregue","recompra","cancelado","devolvido"]

def mirror_status_to_whatsapp_panel(payload):
    try:
        url = os.getenv("VITALISMEN_INTERNAL_STATUS_SYNC_URL", "http://127.0.0.1:3001/api/whatsapp/internal/admin-status-sync").strip()
        if not url:
            return {"ok": False, "skipped": True, "reason": "disabled"}
        response = requests.post(url, json=payload, timeout=4)
        try:
            body = response.json()
        except Exception:
            body = {"text": response.text[:300]}
        return {"ok": response.ok, "status_code": response.status_code, "body": body}
    except Exception as exc:
        try:
            app.logger.warning("Falha ao espelhar status no painel WhatsApp: %s", exc)
        except Exception:
            pass
        return {"ok": False, "error": str(exc)}

# =========================
# META / FACEBOOK CAPI (TREINO)
# =========================
FB_PIXEL_ID = (os.getenv("MVP_FB_PIXEL_ID") or "").strip()

# --- pixel por host (CO/EC) ---
_h = (host or "").lower().strip() if "host" in locals() else ""
if "co.maxlien.shop" in _h:
    FB_PIXEL_ID = (os.getenv("MVP_FB_PIXEL_ID_CO") or FB_PIXEL_ID).strip()
elif "ec.maxlien.shop" in _h:
    FB_PIXEL_ID = (os.getenv("MVP_FB_PIXEL_ID_EC") or FB_PIXEL_ID).strip()
# --- fim ---
FB_ACCESS_TOKEN = (os.getenv("MVP_FB_ACCESS_TOKEN") or "").strip()
# === MAXLIEN_FB_CAPI_HELPERS_V1 ===
def _should_fire_purchase(old_status, new_status):
    try:
        o = (old_status or '').strip().lower()
        n = (new_status or '').strip().lower()
        # Dispara somente quando muda para confirmado
        return (o != 'confirmado') and (n == 'confirmado')
    except Exception:
        return False

def _fb_capi_purchase_minimal(lead_row, event_source_url=''):
    # --- pixel por host (CO/EC) calculado no request ---
    _h = (event_source_url or "")
    try:
        _h2 = request.host or ""
    except Exception:
        _h2 = ""
    try:
        _h3 = request.headers.get("Host", "")
    except Exception:
        _h3 = ""
    _h = (str(_h) + " " + str(_h2) + " " + str(_h3)).lower()
    if "co.maxlien.shop" in _h:
        pixel_id = (os.getenv("MVP_FB_PIXEL_ID_CO") or "").strip()
    elif "ec.maxlien.shop" in _h:
        pixel_id = (os.getenv("MVP_FB_PIXEL_ID_EC") or "").strip()
    else:
        pixel_id = ""
    # --- fim ---

    """
    Envia evento Purchase via Meta Conversions API (Pixel).
    Retorna (ok: bool, why: str).
    """
    try:
        import time, json, hashlib
        token = (os.getenv('MVP_FB_ACCESS_TOKEN') or '').strip()
        test_code = (os.getenv('MVP_FB_TEST_EVENT_CODE') or '').strip()
        if not pixel_id or not token:
            return (False, 'missing_pixel_or_token')

        phone = str((lead_row or {}).get('phone') or '').strip()
        # normalizar: manter só dígitos
        phone_digits = ''.join([c for c in phone if c.isdigit()])
        if not phone_digits:
            return (False, 'missing_phone')

        # hash SHA256 do telefone (requisito do CAPI para user_data)
        ph = hashlib.sha256(phone_digits.encode('utf-8')).hexdigest()

        value_raw = (lead_row or {}).get('product_value')
        try:
            value = float(value_raw) if value_raw is not None and str(value_raw).strip() != '' else 0.0
        except Exception:
            value = 0.0

        payload = {
            'data': [{
                'event_name': 'Purchase',
                'event_time': int(time.time()),
                'action_source': 'website',
                'event_source_url': event_source_url or '',
                'user_data': {
                    'ph': [ph],
                },
                'custom_data': {
                    'currency': 'USD',
                    'value': value,
                },
            }],
        }
        if test_code:
            payload['test_event_code'] = test_code

        url = f'https://graph.facebook.com/v18.0/{pixel_id}/events'

        # tentar requests primeiro; fallback urllib
        try:
            import requests
            r = requests.post(url, params={'access_token': token}, json=payload, timeout=8)
            ok = (200 <= r.status_code < 300)
            if ok:
                return (True, 'sent')
            return (False, f'http_{r.status_code}:' + (r.text[:200] if r.text else ''))
        except Exception as e_req:
            try:
                import urllib.request
                import urllib.parse
                data = json.dumps(payload).encode('utf-8')
                q = urllib.parse.urlencode({'access_token': token})
                req = urllib.request.Request(url + '?' + q, data=data, headers={'Content-Type':'application/json'}, method='POST')
                with urllib.request.urlopen(req, timeout=8) as resp:
                    code = getattr(resp, 'status', 200)
                    ok = (200 <= code < 300)
                    return (ok, 'sent' if ok else f'http_{code}')
            except Exception as e_url:
                return (False, f'requests_err={e_req}; urllib_err={e_url}')

    except Exception as e:
        return (False, f'exception:{e}')
# === /MAXLIEN_FB_CAPI_HELPERS_V1 ===

FB_TEST_EVENT_CODE = (os.getenv("MVP_FB_TEST_EVENT_CODE") or "").strip()
FB_CURRENCY = (os.getenv("MVP_FB_CURRENCY") or "USD").strip()

def sha256_hex(s: str) -> str:
    import hashlib
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()

def meta_user_data(phone_norm: str) -> dict:
    # Meta recomenda enviar dados hashed (SHA256). Para telefone, ideal é E164.
    # Aqui usamos o "phone_norm" do seu sistema (digits/+). Removemos espaços e garantimos hashing.
    pn = (phone_norm or "").strip()
    pn = re.sub(r"[^0-9+]", "", pn)
    # Meta aceita "ph" como lista de hashes
    if pn:
        return {"ph": [sha256_hex(pn)]}
    return {}

def send_meta_capi_event(event_name: str, event_id: str, phone_norm: str, value: float | None = None, currency: str | None = None, country: str | None = None) -> dict:

    # PATCH: MX_META_CAPI_RESOLVE_BY_HOST_V1_20260220
    _host = ""
    try:
        if has_request_context():
            _host = (request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or request.host or "")
    except Exception:
        _host = ""

    _country = _mx_country_from_host(_host)

    # Preferir SEMPRE env por país quando houver match
    try:
        pixel_id = _mx_env_country(_country, "MVP_FB_PIXEL_ID", "") or pixel_id
    except Exception:
        pass
    try:
        token = _mx_env_country(_country, "MVP_FB_ACCESS_TOKEN", "") or token
    except Exception:
        pass
    try:
        currency = _mx_env_country(_country, "MVP_FB_CURRENCY", "USD") or currency
    except Exception:
        pass
    # END PATCH: MX_META_CAPI_RESOLVE_BY_HOST_V1_20260220
    # === CAPI_HOST_SCOPE_V1 ===
    # Resolve pixel_id + token por HOST (EC vs CO) — sem mistura
    try:
        from flask import request
        host = (request.headers.get("X-Forwarded-Host")
                or request.headers.get("Host")
                or request.host
                or "").strip().lower()
    except Exception:
        host = ""
    host = host.split(",")[0].strip().split(":")[0]

    pixel_id = ""
    FB_ACCESS_TOKEN = ""

    country_hint = (country or "").strip().upper()
    is_co_country = country_hint == "CO"
    is_ec_country = country_hint == "EC"
    is_co_host = _mx_final90_is_co_host(host)

    if is_co_country or (is_co_host and not is_ec_country):
        pixel_id = (os.getenv("MVP_FB_PIXEL_ID_CO") or PIXEL_CO_DEFAULT).strip()
        FB_ACCESS_TOKEN = (os.getenv("MVP_FB_ACCESS_TOKEN_CO") or "").strip()
    else:
        pixel_id = (os.getenv("MVP_FB_PIXEL_ID_EC") or os.getenv("MVP_FB_PIXEL_ID") or PIXEL_EC_DEFAULT).strip()
        FB_ACCESS_TOKEN = (os.getenv("MVP_FB_ACCESS_TOKEN_EC") or os.getenv("MVP_FB_ACCESS_TOKEN") or "").strip()
    # Se não configurado, não quebra o fluxo
    if not pixel_id or not FB_ACCESS_TOKEN:
        return {
            "ok": False,
            "skipped": True,
            "reason": "fb_not_configured",
            "host": host,
            "pixel": pixel_id
        }

    url = f"https://graph.facebook.com/v20.0/{pixel_id}/events"
    payload = {
        "data": [
            {
                "event_name": event_name,
                "event_time": int(time.time()),
                "action_source": "website",
                "event_id": event_id,
                "user_data": meta_user_data(phone_norm),
            }
        ],
        "access_token": FB_ACCESS_TOKEN,
    }

    if FB_TEST_EVENT_CODE:
        payload["test_event_code"] = FB_TEST_EVENT_CODE

    # Purchase custom_data
    if event_name == "Purchase":
        cd = {}
        if value is not None:
            cd["value"] = float(value)
        cd["currency"] = (currency or FB_CURRENCY or "USD")
        payload["data"][0]["custom_data"] = cd

    try:
        r = requests.post(url, json=payload, timeout=12)
        out = r.json() if r.content else {}
        if r.status_code >= 300:
            # Loga, mas não quebra o fluxo
            try:
                app.logger.error(f"Meta CAPI HTTP {r.status_code}: {str(out)[:400]}")
            except Exception:
                pass
            return {"ok": False, "status": r.status_code, "resp": out}
        # === CAPI_LOG_OK_SAFE_V4 ===
        try:
            app.logger.info("Meta CAPI OK event=%s pixel=%s event_id=%s status=%s" % (event_name, pixel_id, event_id, r.status_code))
        except Exception:
            pass
        return {"ok": True, "resp": out}
    except Exception as e:
        try:
            app.logger.exception(f"Meta CAPI exception: {e}")
        except Exception:
            pass
        return {"ok": False, "error": str(e)[:200]}


def send_meta_capi_purchase_for_lead(lead_row: dict, event_id: str, country: str | None = None) -> dict:
    lead = row_to_dict(lead_row)
    phone_norm = safe_str(lead.get("phone_e164") or lead.get("phone"))
    user_data = meta_user_data(phone_norm)

    for meta_key, lead_key in (
        ("fbp", "fbp"),
        ("fbc", "fbc"),
        ("client_ip_address", "client_ip_address"),
        ("client_user_agent", "client_user_agent"),
    ):
        value = safe_str(lead.get(lead_key))
        if value:
            user_data[meta_key] = value

    try:
        value = float(str(lead.get("product_value") or "0").replace(",", "."))
    except Exception:
        value = 0.0

    country_hint = safe_str(country or lead.get("country")).upper()
    host = ""
    try:
        if has_request_context():
            host = (request.headers.get("X-Forwarded-Host") or request.headers.get("Host") or request.host or "").strip().lower()
    except Exception:
        host = ""
    host = host.split(",")[0].split(":")[0]

    is_co = country_hint == "CO" or (_mx_final90_is_co_host(host) and country_hint != "EC")
    if is_co:
        pixel_id = (os.getenv("MVP_FB_PIXEL_ID_CO") or PIXEL_CO_DEFAULT).strip()
        access_token = (os.getenv("MVP_FB_ACCESS_TOKEN_CO") or "").strip()
        currency = (os.getenv("MVP_FB_CURRENCY_CO") or "COP").strip()
    else:
        pixel_id = (os.getenv("MVP_FB_PIXEL_ID_EC") or os.getenv("MVP_FB_PIXEL_ID") or PIXEL_EC_DEFAULT).strip()
        access_token = (os.getenv("MVP_FB_ACCESS_TOKEN_EC") or os.getenv("MVP_FB_ACCESS_TOKEN") or "").strip()
        currency = (os.getenv("MVP_FB_CURRENCY_EC") or os.getenv("MVP_FB_CURRENCY") or "USD").strip()

    if not pixel_id or not access_token:
        return {"ok": False, "skipped": True, "reason": "fb_not_configured", "pixel": pixel_id}
    if not user_data:
        return {"ok": False, "reason": "missing_user_data"}

    event = {
        "event_name": "Purchase",
        "event_time": int(time.time()),
        "action_source": "website",
        "event_id": event_id,
        "user_data": user_data,
        "custom_data": {
            "currency": currency,
            "value": value,
        },
    }
    source_url = safe_str(lead.get("event_source_url"))
    if source_url:
        event["event_source_url"] = source_url

    payload = {"data": [event], "access_token": access_token}
    if FB_TEST_EVENT_CODE:
        payload["test_event_code"] = FB_TEST_EVENT_CODE

    try:
        url = f"https://graph.facebook.com/v20.0/{pixel_id}/events"
        r = requests.post(url, json=payload, timeout=12)
        out = r.json() if r.content else {}
        if r.status_code >= 300:
            try:
                app.logger.error("FB_CAPI_PURCHASE_ERROR lead=%s event_id=%s status=%s resp=%s" % (lead.get("id"), event_id, r.status_code, str(out)[:400]))
            except Exception:
                pass
            return {"ok": False, "status": r.status_code, "resp": out, "event_id": event_id}
        try:
            app.logger.info("FB_CAPI_PURCHASE_SENT lead=%s event_id=%s pixel=%s status=%s" % (lead.get("id"), event_id, pixel_id, r.status_code))
        except Exception:
            pass
        return {"ok": True, "resp": out, "event_id": event_id}
    except Exception as e:
        try:
            app.logger.exception("FB_CAPI_PURCHASE_ERROR lead=%s event_id=%s error=%s" % (lead.get("id"), event_id, e))
        except Exception:
            pass
        return {"ok": False, "error": str(e)[:200], "event_id": event_id}


# Exibição de horário fixa (sem zoneinfo)
# Brasil: -3 | Equador: -5
DISPLAY_TZ_OFFSET_HOURS = int(os.getenv("MVP_DISPLAY_TZ_OFFSET_HOURS", "-3"))


# =========================
# APP
# =========================

# =========================
# PRICE TABLE (country + qty)
# =========================

app = Flask(__name__, static_folder='static')

# === ADMIN BYPASS VIA X-REMOTE-USER ===
@app.before_request
def _admin_bypass_via_nginx():
    if request.path.startswith("/admin/") and request.path != "/admin/login":
        ru = request.headers.get("X-Remote-User", "").strip()
        if ru:
            session["admin_ok"] = True



ENABLE_TREINO = os.getenv("MVP_ENABLE_TREINO","0") == "1"
import logging, sys

# Garantir que app.logger.info(...) apareça no journalctl (systemd)
app.logger.setLevel(logging.INFO)

if not any(isinstance(h, logging.StreamHandler) for h in app.logger.handlers):
    _h = logging.StreamHandler(sys.stdout)
    _h.setLevel(logging.INFO)
    _h.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s in %(module)s: %(message)s"))
    app.logger.addHandler(_h)

app.logger.propagate = True

app.secret_key = SECRET_KEY
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)



# === MAXLIEN_HOST_DB_LOG_V1 ===
@app.before_request
def _log_host_and_db():
    if True:  # FIX: try sem except/finally removido        pass  # AUTO-FIX: empty try block
# from flask import Flask, request, session, redirect, url_for, render_template, render_template_string
        app.logger.info(
            "REQ host=%s hdr_host=%s xfh=%s db=%s path=%s",
            getattr(request, "host", ""),
            request.headers.get("Host", ""),
            request.headers.get("X-Forwarded-Host", ""),
            get_db_path(),
            getattr(request, "path", ""),
        )
# ORPHAN_HANDLER_REMOVED:     except Exception:
# ORPHAN_HANDLER_REMOVED:         pass

# =========================
# DB
# =========================
def db_conn():
    db_path = globals().get("DB_PATH")
    # override DB por request (setado em api_lead)
    try:
        from flask import g
        _ov = getattr(g, "_db_path_override", None)
        if _ov:
            db_path = _ov
    except Exception:
        pass
    conn = sqlite3.connect(db_path or get_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema():
    conn = db_conn()
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE IF NOT EXISTS leads ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "name TEXT,"
        "phone TEXT,"
        "city TEXT,"
        "province TEXT,"
        "address TEXT,"
        "product_qty TEXT,"
        "product_value TEXT,"
        "status TEXT,"
        "country TEXT,"
        "created_at TEXT"
        ")"
    )
    ensure_leads_tracking_columns(conn)
    ensure_master_history_tables(conn)
    conn.commit()
    conn.close()

# =========================
# UTIL
# =========================
def safe_str(x):
    if x is None:
        return ""
    return str(x).strip()

LEAD_TRACKING_COLUMNS = {
    "fbp": "TEXT",
    "fbc": "TEXT",
    "fbclid": "TEXT",
    "utm_source": "TEXT",
    "utm_campaign": "TEXT",
    "utm_content": "TEXT",
    "client_ip_address": "TEXT",
    "client_user_agent": "TEXT",
    "event_source_url": "TEXT",
    "buy_later_followup_at": "TEXT",
    "buy_later_notified_at": "TEXT",
}

def ensure_leads_tracking_columns(conn):
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(leads)")
    existing = {str(row[1]) for row in cur.fetchall()}
    for col, col_type in LEAD_TRACKING_COLUMNS.items():
        if col not in existing:
            cur.execute(f"ALTER TABLE leads ADD COLUMN {col} {col_type}")
    ensure_created_at_freeze(conn)

def ensure_created_at_freeze(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TRIGGER IF NOT EXISTS leads_freeze_created_at
        BEFORE UPDATE OF created_at ON leads
        FOR EACH ROW
        WHEN COALESCE(OLD.created_at, '') <> ''
             AND COALESCE(NEW.created_at, '') <> COALESCE(OLD.created_at, '')
        BEGIN
            SELECT RAISE(ABORT, 'data_entrada_original_bloqueada');
        END
    """)

def ensure_master_history_tables(conn):
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lead_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            old_status TEXT,
            new_status TEXT,
            action TEXT,
            created_at TEXT
        )
    """)
    ensure_created_at_freeze(conn)

def record_status_snapshot(cur, lead_id, status, action="initial_state", created_at=None):
    if not lead_id:
        return
    new = safe_str(status or "novo").lower() or "novo"
    stamp = created_at or datetime.now(timezone.utc).isoformat()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lead_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            old_status TEXT,
            new_status TEXT,
            action TEXT,
            created_at TEXT
        )
    """)
    cur.execute(
        "INSERT INTO lead_status_history (lead_id, old_status, new_status, action, created_at) VALUES (?, ?, ?, ?, ?)",
        (int(lead_id), "", new, action, stamp)
    )
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lead_activity_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            activity_type TEXT,
            detail TEXT,
            created_at TEXT
        )
    """)

def record_status_change(cur, lead_id, old_status, new_status, action="status_change"):
    old = safe_str(old_status).lower()
    new = safe_str(new_status).lower()
    if not lead_id or old == new:
        return
    now = datetime.now(timezone.utc).isoformat()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS lead_status_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER NOT NULL,
            old_status TEXT,
            new_status TEXT,
            action TEXT,
            created_at TEXT
        )
    """)
    cur.execute(
        "INSERT INTO lead_status_history (lead_id, old_status, new_status, action, created_at) VALUES (?, ?, ?, ?, ?)",
        (int(lead_id), old, new, action, now)
    )

def status_change_blocked(old_status, new_status):
    old = safe_str(old_status).lower()
    new = safe_str(new_status).lower()
    if not old or not new or old == new:
        return False, ""
    # Operacao manual manda no status: se o atendente confirmou uma recompra
    # ou uma correcao, o painel nao pode desfazer e voltar para entregue.
    if old in {"entregue", "cancelado", "devolvido", "recompra"} and new in {"novo", "atendendo"}:
        return True, "status_final_nao_pode_voltar_para_funil"
    return False, ""

def default_buy_later_followup_at():
    target = datetime.now(timezone(timedelta(hours=-3))) + timedelta(days=1)
    target = target.replace(hour=9, minute=0, second=0, microsecond=0)
    return target.isoformat()

def _parse_buy_later_followup_dt(value):
    raw = safe_str(value)
    if not raw:
        return None
    normalized = raw.replace("Z", "+00:00").strip()
    for fmt in ("%d-%m-%Y %H:%M", "%d/%m/%Y %H:%M", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(normalized, fmt)
            if fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
                dt = dt.replace(hour=9, minute=0)
            return dt.replace(tzinfo=timezone(timedelta(hours=-3)))
        except Exception:
            pass
    try:
        dt = datetime.fromisoformat(normalized.replace(" ", "T"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=-3)))
        return dt
    except Exception:
        return None

def normalize_buy_later_followup_at(value):
    dt = _parse_buy_later_followup_dt(value)
    if not dt:
        return ""
    return dt.astimezone(timezone.utc).isoformat()

def format_buy_later_followup(value):
    dt = _parse_buy_later_followup_dt(value)
    if not dt:
        return safe_str(value)
    br = dt.astimezone(timezone(timedelta(hours=-3)))
    return br.strftime("%d-%m-%Y %H:%M")

def get_client_ip_address():
    try:
        return _final90_get_ip(request)
    except Exception:
        return safe_str(getattr(request, "remote_addr", ""))

def lead_tracking_from_request(data):
    return {
        "fbp": safe_str(data.get("fbp")),
        "fbc": safe_str(data.get("fbc")),
        "fbclid": safe_str(data.get("fbclid")),
        "utm_source": safe_str(data.get("utm_source")),
        "utm_campaign": safe_str(data.get("utm_campaign")),
        "utm_content": safe_str(data.get("utm_content")),
        "client_ip_address": safe_str(data.get("client_ip_address")) or get_client_ip_address(),
        "client_user_agent": safe_str(data.get("client_user_agent")) or safe_str(data.get("user_agent")) or safe_str(request.headers.get("User-Agent", "")),
        "event_source_url": safe_str(data.get("event_source_url")),
    }

def row_to_dict(row):
    if not row:
        return {}
    try:
        return {k: row[k] for k in row.keys()}
    except Exception:
        return dict(row)


ensure_schema()


def normalize_phone(raw):
    raw = (raw or "").strip()
    return re.sub(r"[^\d+]", "", raw)

def normalize_phone_by_country(raw: str, country: str) -> str:
    c = (country or "").strip().upper()
    digits = re.sub(r"[^0-9]", "", (raw or "").strip())
    if not digits:
        return ""
    if c == "CO":
        if digits.startswith("593"):
            digits = digits[3:]
        if digits.startswith("57") and len(digits) >= 12:
            return "+" + digits
        if len(digits) == 10 and digits.startswith("3"):
            return "+57" + digits
        return digits
    # default EC
    if digits.startswith("57"):
        digits = digits[2:]
    if digits.startswith("593") and len(digits) >= 12:
        return "+593" + digits[3:]
    if len(digits) == 10 and digits.startswith("09"):
        return "+593" + digits[1:]
    if len(digits) == 9 and digits.startswith("9"):
        return "+593" + digits
    return digits


def _phone_dedupe_tail(raw: str, country: str = "") -> str:
    digits = _digits(raw)
    if not digits:
        return ""
    c = (country or "").strip().upper()
    if c == "CO":
        if digits.startswith("57") and len(digits) >= 12:
            digits = digits[2:]
        return digits[-10:] if len(digits) >= 10 else digits
    if c == "EC":
        if digits.startswith("593"):
            digits = digits[3:]
        if digits.startswith("0") and len(digits) >= 10:
            digits = digits[1:]
        return digits[-9:] if len(digits) >= 9 else digits
    return digits[-10:] if len(digits) >= 10 else digits


def _operational_phone_aliases() -> set[str]:
    default_numbers = "553183002800,553171862958,5515991418416,5515998038637,593999729030"
    raw = (os.getenv("MVP_BLOCKED_FORM_PHONES") or default_numbers).replace(";", ",")
    aliases: set[str] = set()
    for item in raw.split(","):
        digits = _digits(item)
        if not digits:
            continue
        aliases.add(digits)
        if len(digits) >= 10:
            aliases.add(digits[-10:])
        if len(digits) >= 9:
            aliases.add(digits[-9:])
    return aliases


def is_blocked_operational_phone(phone_norm: str, country: str = "") -> bool:
    digits = _digits(phone_norm)
    if not digits:
        return False
    aliases = _operational_phone_aliases()
    candidates = {digits}
    if len(digits) >= 10:
        candidates.add(digits[-10:])
    if len(digits) >= 9:
        candidates.add(digits[-9:])
    tail = _phone_dedupe_tail(phone_norm, country)
    if tail:
        candidates.add(tail)
    return bool(candidates & aliases)


def find_existing_lead_id_by_phone(phone_norm: str, country: str = "", db_path: str | None = None, exclude_id: int | None = None) -> int | None:
    tail = _phone_dedupe_tail(phone_norm, country)
    if not tail:
        return None
    conn = None
    try:
        conn = sqlite3.connect(db_path) if db_path else db_conn()
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT id, phone, phone_e164, country FROM leads ORDER BY id DESC")
        for row in cur.fetchall():
            row_id = int(row["id"])
            if exclude_id and row_id == int(exclude_id):
                continue
            row_country = (row["country"] or country or "").strip().upper()
            for value in (row["phone"], row["phone_e164"]):
                if _phone_dedupe_tail(value, row_country or country) == tail:
                    return row_id
    except Exception:
        return None
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass
    return None



def now_iso_utc():
    return datetime.now(timezone.utc).isoformat()


def fmt_created_at_display(iso_str):
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(iso_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt_utc = dt.astimezone(timezone.utc)
        dt_disp = dt_utc + timedelta(hours=DISPLAY_TZ_OFFSET_HOURS)
        return dt_disp.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(iso_str)


def admin_required():
    if os.getenv("MVP_ADMIN_AUTH_DISABLED", "1").lower() not in ("0", "false", "no"):
        session["admin_ok"] = True
        return None
    if session.get("admin_ok") is True:
        return None
    try:
        from urllib.parse import quote as _quote
        next_url = _quote(request.full_path.rstrip("?") or "/admin/dashboard", safe="")
        return redirect(f"/admin/login?next={next_url}", code=302)
    except Exception:
        return redirect("/admin/login", code=302)

def status_class(status_value):
    s = str(status_value or "novo").strip().lower()
    return {
        "novo": "st-gray",
        "atendendo": "st-atendendo",
        "comprar_depois": "st-yellow",
        "confirmado": "st-confirmado",
        "pedido_enviado": "st-pedido_enviado",
        "entregue": "st-green",
        "recompra": "st-recompra",
        "finalizado": "st-green",
        "cancelado": "st-red",
        "devolvido": "st-devolvido",
    }.get(s, "st-gray")

DASHBOARD_HTML = """<!doctype html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Painel Maxlien</title>
<style>
body{font-family:Arial;background:#0b0b0b;color:#fff;margin:0}
.wrap{max-width:none;margin:0 auto;padding:10px}
.top{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:10px}
.badge{display:inline-block;padding:6px 12px;border-radius:999px;font-weight:800;font-size:13px}
.badge-ec{background:#14532d;color:#d1fae5}.badge-co{background:#1d4ed8;color:#dbeafe}
h2{margin:6px 0 4px;font-size:24px}.muted{color:#aaa;margin:0 0 10px;font-size:13px}
.table-wrap{width:100%;overflow:auto;border-radius:12px;border:1px solid #222;background:#111}
table{width:100%;border-collapse:collapse;min-width:980px;background:#111}
th,td{border-bottom:1px solid #222;padding:8px 8px;text-align:left;font-size:12px;vertical-align:top}
th{background:#151515;position:sticky;top:0;z-index:1}.nowrap{white-space:nowrap}.addr{max-width:210px}
a{color:#93c5fd;text-decoration:none}.btn{display:inline-block;border:1px solid #334155;border-radius:8px;padding:6px 9px;background:#111827;color:#e5e7eb;font-weight:700;font-size:12px}
.action-cell{display:flex;gap:6px;align-items:center;flex-wrap:wrap}.inline-delete{display:inline}.btn-danger{border-color:#7f1d1d;background:#450a0a;color:#fecaca;cursor:pointer}
.status-pick{min-width:124px;padding:7px 8px;border-radius:8px;border:1px solid #333;color:#fff;background:#1f2937;font-weight:800;font-size:12px}
.st-gray{background:#374151}.st-blue{background:#1d4ed8}.st-green{background:#15803d}.st-yellow{background:#ca8a04;color:#111}.st-red{background:#b91c1c}.st-atendendo{background:#0e7490;color:#ecfeff}.st-pedido_enviado{background:#3730a3;color:#eef2ff}.st-confirmado{background:#6d28d9;color:#f5f3ff}.st-recompra{background:#be185d;color:#fdf2f8}.st-devolvido{background:#9a3412;color:#fff7ed}
.badge-saved{display:inline-block;margin-left:8px;font-size:12px;color:#86efac;transition:opacity .2s}
.search-panel{display:flex;align-items:center;justify-content:space-between;gap:10px;margin:0 0 10px;padding:9px;border:1px solid #222;border-radius:12px;background:#111827}
.search-box{position:relative;flex:1;min-width:260px}
.search-icon{position:absolute;left:13px;top:50%;transform:translateY(-50%);color:#f59e0b;font-size:18px;pointer-events:none}
.lead-search{width:100%;padding:10px 12px 10px 38px;border-radius:10px;border:1px solid #334155;background:#0b0b0b;color:#fff;font-size:13px;outline:none}
.lead-search:focus{border-color:#d97745;box-shadow:0 0 0 3px rgba(217,119,69,.16)}
.lead-search::placeholder{color:#8b95a7}.search-count{color:#aaa;font-size:13px;white-space:nowrap}.is-hidden-by-search{display:none}
.btn-new{background:#d97745;border-color:#f59e0b;color:#111;cursor:pointer}
.modal-backdrop{position:fixed;inset:0;display:none;align-items:center;justify-content:center;padding:18px;background:rgba(0,0,0,.72);z-index:20}
.modal-backdrop.is-open{display:flex}.modal{width:min(720px,100%);border:1px solid #334155;border-radius:12px;background:#111827;box-shadow:0 22px 70px rgba(0,0,0,.42);overflow:hidden}
.modal.orders-modal{width:min(1240px,100%);max-height:92vh}.modal.orders-modal.is-maximized{width:calc(100vw - 24px);height:calc(100vh - 24px);max-height:none}.modal.orders-modal.is-maximized .modal-body{min-height:0;display:flex;flex-direction:column}.modal.orders-modal.is-maximized .orders-table-wrap{flex:1;max-height:none}.orders-empty{display:none;padding:16px;border:1px dashed #334155;border-radius:10px;color:#cbd5e1;background:#0b0b0b}.orders-table-wrap{max-height:72vh;overflow:auto;border:1px solid #243044;border-radius:10px;background:#0b0b0b}
.confirmed-controls{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:0 0 10px}.confirmed-controls select,.confirmed-controls input{border:1px solid #334155;border-radius:8px;background:#0b0b0b;color:#fff;padding:9px 10px;font-size:13px;font-weight:800}.confirmed-controls input[hidden]{display:none}.confirmed-controls .hint{color:#94a3b8;font-size:12px}
.confirmed-select{width:18px;height:18px;accent-color:#22c55e}.confirmed-dropi-msg{min-height:18px;color:#cbd5e1;font-size:13px;margin-left:auto}
.btn-confirmed{background:#14532d;border-color:#22c55e;color:#dcfce7;cursor:pointer}
.modal-head{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:14px 16px;border-bottom:1px solid #243044}.modal-head h3{margin:0;font-size:18px}
.modal-body{padding:14px 16px}.form-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}.form-full{grid-column:1/-1}
.field label{display:block;margin:0 0 5px;color:#cbd5e1;font-size:12px;font-weight:800}.field input,.field select{width:100%;box-sizing:border-box;border:1px solid #334155;border-radius:8px;background:#0b0b0b;color:#fff;padding:10px;font-size:13px}
.modal-actions{display:flex;justify-content:flex-end;gap:8px;padding:12px 16px;border-top:1px solid #243044}.modal-msg{min-height:18px;margin-top:10px;color:#fca5a5;font-size:13px}.modal-close{border:0;background:transparent;color:#e5e7eb;font-size:24px;line-height:1;cursor:pointer}
@media(max-width:720px){.wrap{padding:12px}h2{font-size:24px}th,td{font-size:13px;padding:9px}.table-wrap{border-radius:8px}.search-panel{align-items:stretch;flex-direction:column}.search-box{min-width:0}.search-count{white-space:normal}.form-grid{grid-template-columns:1fr}.modal-actions{flex-direction:column}.modal-actions .btn{width:100%}}
/* === CODEX_STATUS_PALETTE_20260524 === */
.st-atendendo{background:#0e7490!important;border-color:#a5f3fc!important;color:#ecfeff!important}
.st-pedido_enviado{background:#3730a3!important;border-color:#c7d2fe!important;color:#eef2ff!important}
.st-confirmado{background:#6d28d9!important;border-color:#ddd6fe!important;color:#f5f3ff!important}
.st-recompra{background:#be185d!important;border-color:#fbcfe8!important;color:#fdf2f8!important}
.st-devolvido{background:#9a3412!important;border-color:#fed7aa!important;color:#fff7ed!important}
</style></head><body>
<div class="wrap">
{% set host = request.host or '' %}
{% set sigla = 'CO' if 'colombia.maxtourus.com.br' in host else 'EC' %}
<div class="top">
  <div>
    <div class="badge {{ 'badge-co' if sigla == 'CO' else 'badge-ec' }}">Painel Unificado</div>
    <h2>Leads Recentes</h2>
    <p class="muted">Leads e contatos EC/CO no mesmo painel. Dados tecnicos como event_id ficam ocultos.</p>
  </div>
  <div style="display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end"><button type="button" class="btn btn-new" id="open-new-order">Novo pedido</button><button type="button" class="btn btn-confirmed" data-confirmed-country="EC">Pedidos confirmados EC</button><button type="button" class="btn btn-confirmed" data-confirmed-country="CO">Pedidos confirmados CO</button><a class="btn" href="/admin/dashboard">Todos</a><a class="btn" href="/admin/dashboard?country=EC">Leads EC</a><a class="btn" href="/admin/dashboard?country=CO">Leads CO</a><a class="btn" href="/admin/dashboard">Atualizar</a></div>
</div>
<form class="search-panel" method="get" action="/admin/dashboard">
  <input type="hidden" name="country" value="{{ country_filter or 'ALL' }}">
  <div class="search-box">
    <span class="search-icon" aria-hidden="true">⌕</span>
    <input id="lead-search" class="lead-search" type="search" name="q" value="{{ search_query|e }}" autocomplete="off" placeholder="Buscar por nome, telefone, ID, cidade, endereço ou província">
  </div>
  <button type="submit" class="btn">Buscar</button>
  <div class="search-count" id="lead-search-count">Mostrando todos</div>
</form>
<div class="modal-backdrop" id="new-order-modal" aria-hidden="true">
  <div class="modal" role="dialog" aria-modal="true" aria-labelledby="new-order-title">
    <div class="modal-head">
      <h3 id="new-order-title">Novo pedido</h3>
      <button type="button" class="modal-close" id="close-new-order" aria-label="Fechar">×</button>
    </div>
    <form id="new-order-form">
      <div class="modal-body">
        <div class="form-grid">
          <div class="field"><label>País</label><select name="country" id="new-country"><option value="EC">Equador</option><option value="CO">Colômbia</option></select></div>
          <div class="field"><label>Quantidade</label><select name="product_qty" id="new-qty"><option value="1">1 frasco</option><option value="2">2 frascos</option><option value="3">3 frascos</option><option value="6">6 frascos</option></select></div>
          <div class="field"><label>Nome</label><input name="name" autocomplete="name" required></div>
          <div class="field"><label>Telefone</label><input name="phone" autocomplete="tel" required></div>
          <div class="field form-full"><label>Endereço</label><input name="address" autocomplete="street-address"></div>
          <div class="field"><label>Cidade</label><input name="city" autocomplete="address-level2"></div>
          <div class="field"><label>Província</label><input name="province" autocomplete="address-level1"></div>
          <div class="field"><label>Valor</label><input name="product_value" id="new-value" readonly></div>
        </div>
        <div class="modal-msg" id="new-order-msg"></div>
      </div>
      <div class="modal-actions">
        <button type="button" class="btn" id="cancel-new-order">Cancelar</button>
        <button type="submit" class="btn btn-new" id="submit-new-order">Enviar pedido</button>
      </div>
    </form>
  </div>
</div>
<div class="modal-backdrop" id="confirmed-orders-modal" aria-hidden="true">
  <div class="modal orders-modal" role="dialog" aria-modal="true" aria-labelledby="confirmed-orders-title">
    <div class="modal-head">
      <h3 id="confirmed-orders-title">Pedidos confirmados hoje</h3>
      <div style="display:flex;align-items:center;gap:8px"><button type="button" class="btn" id="maximize-confirmed-orders">Maximizar</button><button type="button" class="modal-close" id="close-confirmed-orders" aria-label="Fechar">×</button></div>
    </div>
    <div class="modal-body">
      <div class="confirmed-controls">
        <select id="confirmed-period-select" aria-label="Período dos pedidos confirmados">
          <option value="today">Hoje</option>
          <option value="yesterday">Ontem</option>
          <option value="custom">Personalizado</option>
        </select>
        <input id="confirmed-custom-date" type="date" hidden aria-label="Data personalizada">
        <span class="hint">Filtro aplicado dentro dos pedidos confirmados.</span>
      </div>
      <div class="orders-empty" id="confirmed-orders-empty">Nenhum pedido confirmado para este filtro.</div>
      <div class="orders-table-wrap">
        <table>
          <thead><tr><th>Sel.</th><th>ID</th><th>Data</th><th>Nome</th><th>Telefone</th><th>Endereço</th><th>Cidade</th><th>Província</th><th>Qtd</th><th>Valor</th><th>Status</th><th>País</th><th>Ação</th></tr></thead>
          <tbody>
          {% for country, country_rows in confirmed_today.items() %}
            {% for r in country_rows %}
            <tr data-confirmed-row="{{ country }}" data-confirmed-date="{{ (r[10] or '')[:10] }}">
              <td class="nowrap"><input class="confirmed-select" type="checkbox" data-confirmed-select data-lead-id="{{ r[0] }}" data-country="{{ r[9] or country }}"></td>
              <td class="nowrap">{{ r[0] }}</td>
              <td class="nowrap">{{ fmt_created_at_display(r[10] or '') }}</td>
              <td>{{ r[1] or '' }}</td>
              <td class="nowrap">{{ r[2] or '' }}</td>
              <td class="addr">{{ r[3] or '' }}</td>
              <td>{{ r[4] or '' }}</td>
              <td>{{ r[5] or '' }}</td>
              <td class="nowrap">{{ r[6] or '' }}</td>
              <td class="nowrap">{{ '%.2f'|format(r[7] or 0) }}</td>
              <td><span class="badge {{ status_class(r[8] or 'confirmado') }}">{{ r[8] or 'confirmado' }}</span></td>
              <td class="nowrap">{{ r[9] or country }}</td>
              <td class="nowrap"><div class="action-cell"><a class="btn" href="/admin/edit/{{ r[0] }}?country={{ r[9] or country }}">Editar</a><form class="inline-delete" method="post" action="/admin/delete/{{ r[0] }}?country={{ r[9] or country }}" onsubmit="return confirm('Excluir este cliente incompleto? Esta ação não volta.');"><button type="submit" class="btn btn-danger">Excluir</button></form></div></td>
            </tr>
            {% endfor %}
          {% endfor %}
          </tbody>
        </table>
      </div>
    </div>
    <div class="modal-actions">
      <button type="button" class="btn" id="select-all-confirmed-orders">Selecionar todos</button>
      <button type="button" class="btn btn-confirmed" id="send-confirmed-dropi">Enviar Dropi</button>
      <span class="confirmed-dropi-msg" id="confirmed-dropi-msg"></span>
      <button type="button" class="btn" id="done-confirmed-orders">Fechar</button>
    </div>
  </div>
</div>
<div class="table-wrap">
<table>
<thead><tr><th>ID</th><th>Data</th><th>Nome</th><th>Telefone</th><th>Endereço</th><th>Cidade</th><th>Província</th><th>Qtd</th><th>Valor</th><th>Status</th><th>País</th><th>Ação</th></tr></thead>
<tbody>
{% for r in rows %}
<tr data-search="{{ (r[0] ~ ' ' ~ (r[1] or '') ~ ' ' ~ (r[2] or '') ~ ' ' ~ (r[3] or '') ~ ' ' ~ (r[4] or '') ~ ' ' ~ (r[5] or '') ~ ' ' ~ (r[6] or '') ~ ' ' ~ (r[8] or '') ~ ' ' ~ (r[9] or sigla))|lower|e }}">
<td class="nowrap">{{ r[0] }}</td>
<td class="nowrap">{{ fmt_created_at_display(r[10] or '') }}</td>
<td>{{ r[1] or '' }}</td>
<td class="nowrap">{{ r[2] or '' }}</td>
<td class="addr">{{ r[3] or '' }}</td>
<td>{{ r[4] or '' }}</td>
<td>{{ r[5] or '' }}</td>
<td class="nowrap">{{ r[6] or '' }}</td>
<td class="nowrap">{{ '%.2f'|format(r[7] or 0) }}</td>
<td>
{% set current_status = (r[8] or 'novo') %}
<select class="status-pick {{ status_class(current_status) }}" data-lead-id="{{ r[0] }}" data-country="{{ r[9] or sigla }}" data-prev="{{ current_status }}">
{% if current_status not in statuses %}
  <option value="{{ current_status }}" selected>{{ current_status.replace('_', ' ') }} antigo</option>
{% endif %}
{% for s in statuses %}
  <option value="{{ s }}" {% if current_status == s %}selected{% endif %}>{{ s.replace('_', ' ') }}</option>
{% endfor %}
</select>
<span class="followup-date" data-followup-date>{{ format_buy_later_followup(r[11] if r|length > 11 else '') }}</span>
</td>
<td class="nowrap">{{ r[9] or sigla }}</td>
<td class="nowrap"><div class="action-cell"><a class="btn" href="/admin/edit/{{ r[0] }}?country={{ r[9] or sigla }}">Editar</a><form class="inline-delete" method="post" action="/admin/delete/{{ r[0] }}?country={{ r[9] or sigla }}" onsubmit="return confirm('Excluir este cliente incompleto? Esta ação não volta.');"><button type="submit" class="btn btn-danger">Excluir</button></form></div></td>
</tr>
{% endfor %}
</tbody>
</table>
</div>
</div>
<script>
(function(){
  const mapClass = (v) => {
    v = (v || '').toString().trim().toLowerCase();
    const m = {"novo":"st-gray","atendendo":"st-atendendo","comprar_depois":"st-yellow","confirmado":"st-confirmado","pedido_enviado":"st-pedido_enviado","entregue":"st-green","recompra":"st-recompra","finalizado":"st-green","cancelado":"st-red","devolvido":"st-devolvido"};
    return m[v] || "st-gray";
  };
  const applyColor = (sel) => { sel.className = "status-pick " + mapClass(sel.value); };
  const showSaved = (sel, ok, msg) => {
    let b = sel.parentElement.querySelector(".badge-saved");
    if(!b){ b = document.createElement("span"); b.className = "badge-saved"; sel.parentElement.appendChild(b); }
    b.textContent = ok ? "salvo" : ("erro" + (msg ? (": "+msg) : ""));
    b.style.opacity = "0.95";
    clearTimeout(b._t);
    b._t = setTimeout(()=>{ b.style.opacity = "0"; }, ok ? 900 : 2200);
  };
  const defaultFollowupInput = () => {
    const d = new Date();
    d.setDate(d.getDate() + 1);
    d.setHours(9, 0, 0, 0);
    const pad = (n) => String(n).padStart(2, "0");
    return pad(d.getDate()) + "-" + pad(d.getMonth() + 1) + "-" + d.getFullYear() + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  };
  const parseBrazilFollowupInput = (value) => {
    const raw = (value || "").toString().trim();
    let m = raw.match(/^(\d{2})[-\/](\d{2})[-\/](\d{4})(?:\s+(\d{2}):(\d{2}))?$/);
    if(m){
      const dd = m[1], mm = m[2], yyyy = m[3], hh = m[4] || "09", min = m[5] || "00";
      const parsed = new Date(`${yyyy}-${mm}-${dd}T${hh}:${min}:00-03:00`);
      return Number.isNaN(parsed.getTime()) ? "" : parsed.toISOString();
    }
    m = raw.match(/^(\d{4})-(\d{2})-(\d{2})(?:[ T](\d{2}):(\d{2}))?$/);
    if(m){
      const yyyy = m[1], mm = m[2], dd = m[3], hh = m[4] || "09", min = m[5] || "00";
      const parsed = new Date(`${yyyy}-${mm}-${dd}T${hh}:${min}:00-03:00`);
      return Number.isNaN(parsed.getTime()) ? "" : parsed.toISOString();
    }
    const parsed = new Date(raw);
    return Number.isNaN(parsed.getTime()) ? "" : parsed.toISOString();
  };
  const askFollowupAt = () => {
    const raw = window.prompt("Data para retomar este cliente (ex: 25-05-2026 09:00). Fuso Brasil.", defaultFollowupInput());
    if(raw === null) return "";
    const iso = parseBrazilFollowupInput(raw);
    if(!raw.trim() || !iso){
      window.alert("Data invalida. Use o formato 25-05-2026 09:00.");
      return "";
    }
    return iso;
  };
  async function saveStatus(id, status, country, followupAt){
    const body = { id, status, country };
    if(followupAt) body.followup_at = followupAt;
    const r = await fetch("/admin/api/status", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify(body) });
    const j = await r.json().catch(()=>({ok:false, error:"json"}));
    if(!r.ok || !j.ok) throw new Error(j.error || ("http_"+r.status));
    return j;
  }
  const normalizeSearch = (value) => (value || "").toString().normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().trim();
  const PRICE_TABLE = {
    EC: { "1": 39.99, "2": 69.99, "3": 95.99, "6": 167.99 },
    CO: { "1": 170000, "2": 300000, "3": 350000, "6": 584000 }
  };
  const setupNewOrder = () => {
    const openBtn = document.getElementById("open-new-order");
    const modal = document.getElementById("new-order-modal");
    const form = document.getElementById("new-order-form");
    const closeBtn = document.getElementById("close-new-order");
    const cancelBtn = document.getElementById("cancel-new-order");
    const countryEl = document.getElementById("new-country");
    const qtyEl = document.getElementById("new-qty");
    const valueEl = document.getElementById("new-value");
    const msg = document.getElementById("new-order-msg");
    const submit = document.getElementById("submit-new-order");
    if(!openBtn || !modal || !form) return;
    const urlCountry = new URLSearchParams(window.location.search).get("country");
    if(urlCountry === "CO" || urlCountry === "EC") countryEl.value = urlCountry;
    const updateValue = () => {
      const c = String(countryEl.value || "EC").toUpperCase();
      const q = String(qtyEl.value || "1");
      const price = (PRICE_TABLE[c] || {})[q];
      valueEl.value = c === "CO" ? String(Math.round(price || 0)) : Number(price || 0).toFixed(2);
    };
    const open = () => { msg.textContent = ""; updateValue(); modal.classList.add("is-open"); modal.setAttribute("aria-hidden","false"); setTimeout(()=>form.elements.name.focus(), 30); };
    const close = () => { modal.classList.remove("is-open"); modal.setAttribute("aria-hidden","true"); };
    openBtn.addEventListener("click", open);
    closeBtn.addEventListener("click", close);
    cancelBtn.addEventListener("click", close);
    modal.addEventListener("click", (event) => { if(event.target === modal) close(); });
    document.addEventListener("keydown", (event) => { if(event.key === "Escape" && modal.classList.contains("is-open")) close(); });
    countryEl.addEventListener("change", updateValue);
    qtyEl.addEventListener("change", updateValue);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      msg.textContent = "";
      submit.disabled = true;
      submit.textContent = "Enviando...";
      const payload = Object.fromEntries(new FormData(form).entries());
      try{
        const r = await fetch("/admin/pedido-rapido", { method:"POST", headers:{ "Content-Type":"application/json" }, body: JSON.stringify(payload) });
        const j = await r.json().catch(()=>({ok:false,error:"json"}));
        if(!r.ok || !j.ok) throw new Error(j.error || ("http_" + r.status));
        window.location.href = "/admin/dashboard?country=" + encodeURIComponent(j.country || payload.country || "EC");
      }catch(e){
        msg.textContent = "Não salvou: " + e.message;
      }finally{
        submit.disabled = false;
        submit.textContent = "Enviar pedido";
      }
    });
    updateValue();
  };
  const setupLeadSearch = () => {
    const input = document.getElementById("lead-search");
    const count = document.getElementById("lead-search-count");
    const rows = Array.from(document.querySelectorAll("tbody tr[data-search]"));
    if(!input || !count || !rows.length) return;
    const apply = () => {
      const q = normalizeSearch(input.value);
      let visible = 0;
      rows.forEach((row) => {
        const haystack = normalizeSearch(row.getAttribute("data-search") || row.textContent || "");
        const show = !q || haystack.includes(q);
        row.classList.toggle("is-hidden-by-search", !show);
        if(show) visible += 1;
      });
      count.textContent = q ? (visible + " de " + rows.length + " clientes") : ("Mostrando " + rows.length + " clientes");
    };
    input.addEventListener("input", apply);
    input.addEventListener("keydown", (event) => {
      if(event.key === "Escape"){ input.value = ""; apply(); input.blur(); }
    });
    apply();
  };
  const setupConfirmedOrders = () => {
    const modal = document.getElementById("confirmed-orders-modal");
    const title = document.getElementById("confirmed-orders-title");
    const box = modal ? modal.querySelector(".orders-modal") : null;
    const closeBtn = document.getElementById("close-confirmed-orders");
    const doneBtn = document.getElementById("done-confirmed-orders");
    const maximizeBtn = document.getElementById("maximize-confirmed-orders");
    const selectAllBtn = document.getElementById("select-all-confirmed-orders");
    const sendDropiBtn = document.getElementById("send-confirmed-dropi");
    const dropiMsg = document.getElementById("confirmed-dropi-msg");
    const empty = document.getElementById("confirmed-orders-empty");
    const periodSelect = document.getElementById("confirmed-period-select");
    const customDateInput = document.getElementById("confirmed-custom-date");
    const rows = Array.from(document.querySelectorAll("[data-confirmed-row]"));
    const buttons = Array.from(document.querySelectorAll("[data-confirmed-country]"));
    let activeCountry = "EC";
    if(!modal || !buttons.length) return;
    const close = () => { modal.classList.remove("is-open"); modal.setAttribute("aria-hidden","true"); };
    const updateMaximizeLabel = () => {
      if(maximizeBtn && box) maximizeBtn.textContent = box.classList.contains("is-maximized") ? "Restaurar" : "Maximizar";
    };
    const dateValue = (date) => {
      const pad = (value) => String(value).padStart(2, "0");
      return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate());
    };
    const labelByPeriod = { today: "hoje", yesterday: "ontem", custom: "personalizado" };
    const selectedDate = () => {
      const period = periodSelect ? periodSelect.value : "today";
      const date = new Date();
      if(period === "yesterday") date.setDate(date.getDate() - 1);
      if(period === "custom") return customDateInput && customDateInput.value ? customDateInput.value : dateValue(date);
      return dateValue(date);
    };
    const applyFilter = () => {
      const period = periodSelect ? periodSelect.value : "today";
      const targetDate = selectedDate();
      if(customDateInput) customDateInput.hidden = period !== "custom";
      let visible = 0;
      rows.forEach((row) => {
        const show = row.getAttribute("data-confirmed-row") === activeCountry
          && row.getAttribute("data-confirmed-date") === targetDate;
        row.style.display = show ? "" : "none";
        if(!show){
          const cb = row.querySelector("[data-confirmed-select]");
          if(cb) cb.checked = false;
        }
        if(show) visible += 1;
      });
      title.textContent = "Pedidos confirmados " + (labelByPeriod[period] || "hoje") + " " + activeCountry + " (" + visible + ")";
      empty.style.display = visible ? "none" : "block";
    };
    const open = (country) => {
      activeCountry = country || "EC";
      if(periodSelect) periodSelect.value = "today";
      if(customDateInput) { customDateInput.value = dateValue(new Date()); customDateInput.hidden = true; }
      applyFilter();
      modal.classList.add("is-open");
      modal.setAttribute("aria-hidden","false");
    };
    const visibleRows = () => rows.filter((row) => row.style.display !== "none");
    const selectedOrders = () => visibleRows()
      .map((row) => row.querySelector("[data-confirmed-select]:checked"))
      .filter(Boolean)
      .map((cb) => ({
        leadId: cb.getAttribute("data-lead-id") || "",
        country: (cb.getAttribute("data-country") || activeCountry || "EC").toUpperCase()
      }))
      .filter((item) => item.leadId);
    const setDropiMsg = (text, isError) => {
      if(!dropiMsg) return;
      dropiMsg.textContent = text || "";
      dropiMsg.style.color = isError ? "#fca5a5" : "#cbd5e1";
    };
    const submitDropi = async (order) => {
      if(order.country !== "EC") throw new Error("Dropi disponível somente para EC");
      const orderId = "EC-ADMIN-" + order.leadId;
      const dropiErrorMessage = (payload, fallback) => (
        payload?.message || payload?.error || payload?.reason || fallback || "Dropi nao confirmou o envio"
      );
      const dropiConfirmationId = (payload) => (
        payload?.dropiOrderId
        || payload?.result?.dropiOrderId
        || payload?.result?.dropiResponse?.objects?.id
        || payload?.result?.dropiResponse?.objects?.sticker
        || payload?.shipment?.raw?.droppiOrder?.id
        || payload?.shipment?.raw?.dropiOrder?.id
        || ""
      );
      const auth = await fetch("/api/shipments/droppi/ec/orders/" + encodeURIComponent(orderId) + "/authorize-submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note: "Autorizado no Painel Unificado" })
      });
      const authJson = await auth.json().catch(()=>({}));
      if(!auth.ok && !authJson.alreadyAuthorized) throw new Error(dropiErrorMessage(authJson, "autorizar " + orderId));
      const sent = await fetch("/api/shipments/droppi/ec/orders/" + encodeURIComponent(orderId) + "/submit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({})
      });
      const sentJson = await sent.json().catch(()=>({}));
      if(!sent.ok) throw new Error(dropiErrorMessage(sentJson, "enviar " + orderId));
      if(sentJson?.manualSendRequired || sentJson?.paymentRequired || sentJson?.authorizationRequired || sentJson?.ok === false || sentJson?.success === false){
        throw new Error(dropiErrorMessage(sentJson, "Dropi recusou ou pediu revisao manual para " + orderId));
      }
      const confirmationId = dropiConfirmationId(sentJson);
      if(!sentJson?.alreadySubmitted && !confirmationId){
        throw new Error("Dropi nao confirmou ID/guia do pedido " + orderId);
      }
      return { ...sentJson, confirmationId };
    };
    const sendSelectedDropi = async () => {
      const orders = selectedOrders();
      if(!orders.length){ setDropiMsg("Selecione pelo menos um pedido.", true); return; }
      if(!window.confirm("Enviar " + orders.length + " pedido(s) selecionado(s) para Dropi?")) return;
      if(sendDropiBtn) sendDropiBtn.disabled = true;
      if(selectAllBtn) selectAllBtn.disabled = true;
      let ok = 0;
      let failed = 0;
      try{
        for(const order of orders){
          setDropiMsg("Enviando lead #" + order.leadId + "...");
          try{
            const result = await submitDropi(order);
            ok += 1;
            if(result?.confirmationId) setDropiMsg("Lead #" + order.leadId + " enviado. ID Dropi " + result.confirmationId + ".");
          }catch(e){
            failed += 1;
            setDropiMsg("Lead #" + order.leadId + ": " + e.message, true);
          }
        }
        setDropiMsg("Dropi: " + ok + " enviado(s), " + failed + " erro(s).", failed > 0);
      }finally{
        if(sendDropiBtn) sendDropiBtn.disabled = false;
        if(selectAllBtn) selectAllBtn.disabled = false;
      }
    };
    buttons.forEach((button) => button.addEventListener("click", () => open(button.getAttribute("data-confirmed-country") || "EC")));
    if(periodSelect) periodSelect.addEventListener("change", applyFilter);
    if(customDateInput) customDateInput.addEventListener("change", applyFilter);
    if(selectAllBtn) selectAllBtn.addEventListener("click", () => {
      visibleRows().forEach((row) => {
        const cb = row.querySelector("[data-confirmed-select]");
        if(cb) cb.checked = true;
      });
      setDropiMsg(visibleRows().length + " pedido(s) selecionado(s).");
    });
    if(sendDropiBtn) sendDropiBtn.addEventListener("click", sendSelectedDropi);
    closeBtn.addEventListener("click", close);
    doneBtn.addEventListener("click", close);
    if(maximizeBtn && box) maximizeBtn.addEventListener("click", () => { box.classList.toggle("is-maximized"); updateMaximizeLabel(); });
    modal.addEventListener("click", (event) => { if(event.target === modal) close(); });
    document.addEventListener("keydown", (event) => { if(event.key === "Escape" && modal.classList.contains("is-open")) close(); });
  };
  window.addEventListener("DOMContentLoaded", () => {
    setupLeadSearch();
    setupNewOrder();
    setupConfirmedOrders();
    document.querySelectorAll("select.status-pick").forEach((sel) => {
      applyColor(sel);
      sel.addEventListener("change", async () => {
        const id = sel.getAttribute("data-lead-id");
        const prev = sel.getAttribute("data-prev") || sel.value;
        const now = sel.value;
        const country = sel.getAttribute("data-country") || "";
        if(prev !== now && !window.confirm("Deseja alterar os dados/status deste cliente em todos os paineis?")){
          sel.value = prev;
          applyColor(sel);
          return;
        }
        const followupAt = now === "comprar_depois" ? askFollowupAt() : "";
        if(now === "comprar_depois" && !followupAt){
          sel.value = prev;
          applyColor(sel);
          return;
        }
        applyColor(sel); sel.disabled = true;
        try{
          const saved = await saveStatus(id, now, country, followupAt);
          sel.setAttribute("data-prev", now);
          const dateBadge = sel.parentElement.querySelector("[data-followup-date]");
          if(dateBadge) dateBadge.textContent = saved.buy_later_followup_display || saved.buy_later_followup_at || "";
          showSaved(sel, true);
        }
        catch(e){ sel.value = prev; applyColor(sel); showSaved(sel, false, e.message); }
        finally{ sel.disabled = false; }
      });
    });
  });
})();
</script>
</body></html>
"""
# =========================
# API - LEAD
# =========================

def find_recent_duplicate_lead_id(phone_norm: str, window_seconds: int = 6 * 3600) -> int | None:
    """
    Retorna lead_id (id da tabela leads) se encontrar lead com mesmo telefone
    criado nos últimos window_seconds.
    """
    if not phone_norm:
        return None

    try:
        conn = db_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id
            FROM leads
            WHERE phone = ?
              AND (strftime('%s','now') - strftime('%s', created_at)) <= ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (phone_norm, int(window_seconds)),
        )
        row = cur.fetchone()
        return int(row[0]) if row else None
    except Exception:
        return None
# ORPHAN_HANDLER_REMOVED:     finally:
# ORPHAN_HANDLER_REMOVED:         try:
# ORPHAN_HANDLER_REMOVED:             conn.close()
# ORPHAN_HANDLER_REMOVED:         except Exception:
# ORPHAN_HANDLER_REMOVED:             pass

@app.route("/wa")
@app.route("/wa/ec")
def wa_router_public_redirect():
    xfh = (request.headers.get("X-Forwarded-Host") or "").split(",")[0].strip()
    hh = (request.headers.get("Host") or "").split(",")[0].strip()
    host_for_wa = (xfh or hh or (request.host or "")).split(":", 1)[0].strip()
    country = (request.args.get("country") or _mx_country_from_host(host_for_wa) or "EC").strip().upper()
    text = (request.args.get("text") or request.args.get("msg") or "Hola, quiero hacer mi pedido.").strip()
    selected = pick_wa_business_number(country, host_for_wa)
    if not selected:
        return jsonify({"ok": False, "error": "wa_number_not_set"}), 503
    return redirect(wa_me_url(selected, text), code=302)

@app.route("/api/lead", methods=["POST"])
def api_lead():
    data = request.get_json(silent=True) or request.form or {}

    # COUNTRY / DB decision (CO/EC) — prioridade: telefone, fallback: host
    host_raw = (request.headers.get("X-Forwarded-Host") or request.host or "").split(",")[0].strip().lower()
    host_country = "EC" if host_raw.startswith("ec.") else ("CO" if (host_raw.startswith("co.") or host_raw.startswith("colombia.") or "colombia.maxtourus.com.br" in host_raw) else "")
    raw_phone = (data.get("phone") or data.get("whatsapp") or data.get("tel") or data.get("telefone") or "")
    country_dec = (_infer_country_from_phone(raw_phone, host_country) or host_country or "EC").strip().upper()
    # CAPI_CURRENCY_BY_COUNTRYDEC_V1
    currency_host = ("COP" if country_dec == "CO" else "USD")
    data = dict(data)  # garante mutável
    data["country"] = country_dec
    data["pais"] = country_dec
    db_path = _DB_BY_COUNTRY.get(country_dec) or _DB_BY_COUNTRY.get(host_country) or globals().get("DB_PATH")
    # DB override por request (db_conn respeita g._db_path_override)
    try:
        from flask import g
        g._db_path_override = db_path
    except Exception:
        pass
    # --- GUARD: bloquear lead vazio ---
    if not data:
        return jsonify({"ok": False, "error": "empty_payload"}), 400

    _phone_raw = safe_str(data.get("phone"))
    if not (_phone_raw or "").strip():
        return jsonify({"ok": False, "error": "phone_required"}), 400
    # --- /GUARD ---

    # AUTO-PREÇO CO (COP) por quantidade
# AUTO-PREÇO (EC/CO) por quantidade — calculado no backend
    try:
        country_p = (data.get("country") or data.get("pais") or "EC").strip().upper()
        qty_p = str((data.get("product_qty") or "")).strip()
        pv = _price_for(country_p, qty_p)
        if pv is not None:
            data["product_value"] = str(pv)
    except Exception:
        pass


    # === WA TEXT (EC/CO) ===
    name = (data.get("name") or "").strip()
    city = (data.get("city") or "").strip()
    country = (data.get("country") or data.get("pais") or "EC").strip().upper()
    MSG = build_wa_text(country, name, city)
    # === /WA TEXT (EC/CO) ===

    # =========================
    # =========================
    # OPT-IN WhatsApp (TREINO)
    # - Modo WA.ME (sem API): NÃO exige opt-in
    # - Modo Cloud API: exige opt-in
    # =========================
    optin = data.get("whatsapp_optin")

    _host = ((request.headers.get("Host") or request.host or "")).split(":", 1)[0]
    _wa_for_host = (_mvp_wa_number_for_host(_host) or "").strip()

    # Se há WA.ME por host (EC/CO) OU fallback global WA_ME_NUMBER, não exige opt-in.
    if _wa_for_host or WA_ME_NUMBER:
        optin_ok = True
    else:
        optin_ok = optin in (1, "1", True, "true", "True", "on", "yes", "sim")

    if not optin_ok:
        return jsonify({"ok": False, "error": "whatsapp_optin_required"}), 400
    # Normalizações
    phone_raw = safe_str(data.get("phone"))
    phone_norm = normalize_phone_by_country(phone_raw, (data.get("country") or data.get("pais") or host_country or "EC"))
    if is_blocked_operational_phone(phone_norm, country_dec):
        return jsonify({"ok": False, "error": "operational_phone_blocked"}), 400
    # =========================
    # DEDUP por telefone: o mesmo cliente entra uma vez no painel.
    # =========================
    dup_id = find_existing_lead_id_by_phone(phone_norm, country_dec, db_path=db_path)
    if dup_id:
        wa_resp = {"wa_redirect": False, "wa_block_reason": "duplicate"}
        return jsonify({"ok": True, "lead_id": dup_id, "duplicate": True, **wa_resp})

    conn = db_conn()
    cur = conn.cursor()
    ensure_leads_tracking_columns(conn)
    ensure_master_history_tables(conn)
    tracking_fields = lead_tracking_from_request(data)
    # PATCH: MX_LEAD_EVENTID_DBWRITE_V4_3B_20260221_002020
    # event_id p/ dedupe (Pixel <-> CAPI). Se client nao mandar, gera tmp ate termos lead_id.
    client_eid = (safe_str(data.get('event_id')) or '').strip()
    tmp_eid = __import__('uuid').uuid4().hex
    lead_event_id = client_eid if client_eid else f"lead_tmp_{tmp_eid}"

    lead_created_at = now_iso_utc()
    cur.execute(
        "INSERT INTO leads (name, phone, city, province, address, product_qty, product_value, status, country, created_at, event_id, phone_e164, "
        "fbp, fbc, fbclid, utm_source, utm_campaign, utm_content, client_ip_address, client_user_agent, event_source_url) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            safe_str(data.get("name")),
            phone_norm,
            safe_str(data.get("city")),
            safe_str(data.get("province")),
            safe_str(data.get("address")),
            safe_str(data.get("product_qty")),
            safe_str(data.get("product_value")),
            "novo",
            safe_str(data.get("country")) or host_country or "EC",
            lead_created_at,
            # PATCH: MX_LEAD_EVENTID_DBWRITE_V4_3F_20260221_002818
            lead_event_id,
            phone_norm,
            tracking_fields["fbp"],
            tracking_fields["fbc"],
            tracking_fields["fbclid"],
            tracking_fields["utm_source"],
            tracking_fields["utm_campaign"],
            tracking_fields["utm_content"],
            tracking_fields["client_ip_address"],
            tracking_fields["client_user_agent"],
            tracking_fields["event_source_url"],
        ),
    )
    lead_id = cur.lastrowid
    record_status_snapshot(cur, lead_id, "novo", "lead_created", lead_created_at)
    panel_event_id = "%s-ADMIN-%s" % ((safe_str(data.get("country")) or host_country or "EC").strip().upper(), lead_id)
    try:
        cur.execute("UPDATE leads SET event_id=?, updated_at=? WHERE id=?", (panel_event_id, now_iso_utc(), int(lead_id)))
    except Exception:
        pass
    try:
        saved_keys = ",".join([k for k, v in tracking_fields.items() if v])
        app.logger.info("LEAD_TRACKING_FIELDS_SAVED lead=%s fields=%s" % (lead_id, saved_keys))
    except Exception:
        pass
    # PATCH: MX_LEAD_EVENTID_DBWRITE_V4_3B_POST
    # Se o client nao mandou event_id, troca para lead_submit_{lead_id} e persiste
    if not client_eid:
        # PATCH: MX_LEAD_EVENTID_DBWRITE_V4_3F_DISABLE_FORCE_SUBMIT
        # event_id do painel fica canonico como {PAIS}-ADMIN-{lead_id};
        # lead_event_id continua separado apenas para dedupe CAPI.
        pass

    # === CAPI_LEAD_ON_API_LEAD_V3_NEW ===
    # PATCH: MX_LEAD_EVENTID_DBWRITE_V4_3G_20260221_002939
    # Se veio event_id do client, respeita. Senão, usa lead_submit_{lead_id}.
    lead_event_id = (client_eid or ("lead_submit_%s" % (lead_id,)))
    capi_lead_ok = False
    capi_lead_msg = ""

    # =========================================
    # FINAL90: salvar origem (utm/fbclid/fbp/fbc/event_id) + CAPI Lead (dedupe)
    # =========================================
### MAXLIEN_FINAL90_INJECTED_IN_API_LEAD ###
    try:
        # 1) garantir tabela de origem (no MESMO DB do lead)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS lead_origin (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          created_at TEXT,
          lead_id INTEGER,
          phone TEXT,
          event_id TEXT,
          event_source_url TEXT,
          user_agent TEXT,
          ip TEXT,
          utm_source TEXT,
          utm_medium TEXT,
          utm_campaign TEXT,
          utm_content TEXT,
          utm_term TEXT,
          fbclid TEXT,
          fbp TEXT,
          fbc TEXT
        );
        """)

        _ua = request.headers.get("User-Agent", "")
        _ip = _final90_get_ip(request)

        cur.execute(
          """INSERT INTO lead_origin
          (created_at, lead_id, phone, event_id, event_source_url, user_agent, ip,
           utm_source, utm_medium, utm_campaign, utm_content, utm_term,
           fbclid, fbp, fbc)
          VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
          (
            now_iso_utc(),
            int(lead_id),
            phone_norm,
            safe_str(data.get("event_id")),
            safe_str(data.get("event_source_url")),
            _ua,
            _ip,
            safe_str(data.get("utm_source")),
            safe_str(data.get("utm_medium")),
            safe_str(data.get("utm_campaign")),
            safe_str(data.get("utm_content")),
            safe_str(data.get("utm_term")),
            safe_str(data.get("fbclid")),
            safe_str(data.get("fbp")),
            safe_str(data.get("fbc")),
          )
        )
    except Exception as e:
        try:
            app.logger.exception(f"FINAL90 origin save failed lead={lead_id}: {e}")
        except Exception:
            pass

    # 2) CAPI Lead (não quebra o lead se falhar)
    try:
        _ua = request.headers.get("User-Agent", "")
        _ip = _final90_get_ip(request)
        # FINAL90_CAPI_HOSTFIX_V1
        xfh_capi = (request.headers.get("X-Forwarded-Host") or "").split(",")[0].strip()
        hh_capi  = (request.headers.get("Host") or "").split(",")[0].strip()
        host_for_capi = (xfh_capi or hh_capi or (request.host or "")).strip()
        host_for_capi = host_for_capi.split(":",1)[0].strip()
        # PATCH: MX_LEAD_CAPI_DBMARK_V4_6_20260221_003736_USE_RICH
        ok_capi, msg_capi, _capi_st, _capi_fb = _final90_capi_send_lead_rich(host_for_capi, data if isinstance(data, dict) else {}, _ua, _ip)
        _mx_mark_lead_capi(cur, int(lead_id), int(_capi_st or 0), str(_capi_fb or ""))

        # === CAPI_LEAD_V3_TELEMETRY_AFTER_FINAL90 ===
        capi_lead_ok = bool(ok_capi)
        capi_lead_msg = str(msg_capi) if msg_capi is not None else ""
        # opcional: log leve
        # app.logger.info(f"FINAL90 CAPI Lead: {ok_capi} {msg_capi}")
    except Exception as e:
        try:
            app.logger.exception(f"FINAL90 capi failed lead={lead_id}: {e}")
        except Exception:
            pass
### MAXLIEN_FINAL90_INJECTED_IN_API_LEAD ###
    # =========================================
    # /FINAL90
    # =========================================

    conn.commit()
    conn.close()



    # =========================
    # WA.ME REDIRECT (ANTI-SPAM)
    # =========================
    wa_resp = {"wa_redirect": False}
    wa_offer_status = ""
    wa_offer_error = ""
    xfh = (request.headers.get("X-Forwarded-Host") or "").split(",")[0].strip()
    hh = (request.headers.get("Host") or "").split(",")[0].strip()
    host_for_wa = (xfh or hh or (request.host or "")).strip()
    host_for_wa = host_for_wa.split(":", 1)[0].strip()
    wa_num = pick_wa_business_number(country_dec, host_for_wa)

    try:
        if wa_num:
            now_ts = int(time.time())
            ok_offer, reason = wa_offer_check_and_mark(phone_norm, now_ts)
            if ok_offer:
                name = (data.get("name") or "").strip()
                city = (data.get("city") or "").strip()
                country = (data.get("country") or data.get("pais") or "EC").strip().upper()
                msg = build_wa_text(country, name, city)
                try:
                    app.logger.info(f"WA_HOST_FOR_URL host_for_wa={host_for_wa} wa_num={wa_num} WA_DEF={WA_ME_NUMBER}")
                except Exception:
                    pass
                wa_url = wa_me_url(wa_num, msg)
                wa_resp = {
                    "wa_redirect": True,
                    "wa_url": wa_url,
                    "wa_app_url": wa_app_intent_url(wa_num, msg),
                    "wa_selected_number": wa_num,
                    "wa_block_reason": ""
                }
                wa_offer_status = "offered"
            else:
                wa_resp = {"wa_redirect": False, "wa_block_reason": reason}
                wa_offer_status = f"blocked:{reason}"
        else:
            wa_resp = {"wa_redirect": False, "wa_block_reason": "wa_me_number_not_set"}
            wa_offer_status = "missing_business_number"
    except Exception as e:
        wa_resp = {"wa_redirect": False, "wa_block_reason": f"wa_me_error:{e}"}
        wa_offer_error = str(e)
        wa_offer_status = "error"
    # =========================
    # TELEGRAM ALERT (TREINO)
    # =========================
    tg_ok = False
    try:
        msg = (
            "🆕 NOVO LEAD (TREINO)\n\n"
            f"👤 Nome: {data.get('name')}\n"
            f"📞 Telefone: {data.get('phone')}\n"
            f"📍 Cidade: {data.get('city')} / {data.get('province')}\n"
            f"🏠 Endereço: {data.get('address')}\n"
            f"📦 Qtd: {data.get('product_qty')}\n"
            f"💰 Valor: {data.get('product_value')}\n"
            f"🆔 Lead ID: {lead_id}"
        )
        tg_ok = send_telegram_alert(msg)
    except Exception as e:
        try:
            app.logger.exception(f"Erro Telegram no lead {lead_id}: {e}")
        except Exception:
            pass

    # =========================
    # WHATSAPP 1ª MSG (TREINO)
    # =========================
    # =========================
    # WHATSAPP 1ª MSG (TREINO)
    # - Se WA_ME_NUMBER estiver ativo, NÃO tenta Cloud API
    # =========================
    wa_ok = False
    wa_message_id = ""
    wa_error = ""
    phone_e164 = wa_normalize_e164(data.get("phone"))

    wa_log(db_path, lead_id, phone_e164 or phone_norm, "wa_me_redirect", "", "", wa_offer_status or "not_offered", wa_offer_error or wa_resp.get("wa_block_reason", ""))

    if WA_SEND_FIRST_MESSAGE:
        try:
            wa_resp_cloud = wa_send_first_message_template(phone_e164)
            wa_data = wa_resp_cloud.get("data") if isinstance(wa_resp_cloud, dict) else {}

            try:
                wa_message_id = ((wa_data or {}).get("messages") or [{}])[0].get("id") or ""
            except Exception:
                wa_message_id = ""

            wa_ok = bool(wa_resp_cloud.get("ok"))
            if not wa_ok:
                wa_error = str(wa_resp_cloud.get("reason") or wa_resp_cloud.get("error") or "")

            wa_log(
                db_path,
                lead_id,
                phone_e164 or phone_norm,
                "template",
                WA_TEMPLATE_NAME,
                wa_message_id,
                "sent" if wa_ok else (wa_error or "skipped"),
                wa_error,
            )
        except Exception as e:
            wa_error = str(e)
            try:
                app.logger.exception(f"Erro WhatsApp no lead {lead_id}: {e}")
            except Exception:
                pass
            wa_log(db_path, lead_id, phone_e164 or phone_norm, "template", WA_TEMPLATE_NAME, "", "error", wa_error)

    return jsonify({
    "ok": True,
    "lead_id": lead_id,
# === CAPI_LEAD_RETURN_EVENTID_V3 ===
    "event_id": lead_event_id,
    "capi_lead_ok": bool(capi_lead_ok),
    "telegram_ok": tg_ok,
    "wa_ok": wa_ok,
    "wa_message_id": wa_message_id,
    **wa_resp,
    })


# =========================
# ADMIN - LOGIN
# =========================
LOGIN_HTML = """
<!doctype html>
<html><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Admin Login</title>
<style>
body{font-family:Arial;background:#0b0b0b;color:#fff}
.wrap{max-width:520px;margin:18vh auto;padding:18px}
.card{border:1px solid #333;border-radius:14px;background:#111;padding:16px}
label{display:block;margin:10px 0 6px;font-weight:700}
input{width:100%;padding:10px;border-radius:10px;border:1px solid #333;background:#0b0b0b;color:#fff}
button{margin-top:14px;padding:12px;border-radius:12px;border:0;background:#25d366;color:#000;font-weight:800;width:100%}
.err{color:#ff8a8a;margin-top:10px}
/* === STATUS COLORS (Admin Quick Status) === */
.st-novo{background:#2b2b2b;border:1px solid #444;color:#e0e0e0;}
.st-atendendo{background:#0e7490;border:1px solid #a5f3fc;color:#ecfeff;}
.st-pedido_enviado{background:#3730a3;border:1px solid #c7d2fe;color:#eef2ff;}
.st-confirmado{background:#6d28d9;border:1px solid #ddd6fe;color:#f5f3ff;}
.st-recompra{background:#be185d;border:1px solid #fbcfe8;color:#fdf2f8;}
.st-pago{background:#0f5c3b;border:1px solid #1f8a5f;color:#ffffff;}
.st-entregue{background:#124c3b;border:1px solid #1f7a60;color:#ffffff;}
.st-devolvido{background:#5a3b13;border:1px solid #8b5a1e;color:#fff3e0;}
.st-cancelado{background:#5a1a1a;border:1px solid #8b2a2a;color:#ffecec;}

/* badge padrão */
.st-novo,.st-atendendo,.st-pedido_enviado,.st-confirmado,.st-recompra,.st-pago,.st-entregue,.st-devolvido,.st-cancelado{
  padding:6px 12px;border-radius:999px;font-weight:800;text-transform:lowercase;display:inline-block
}

/* select do status (dropdown) */
.status-pick{
  padding:6px 10px;border-radius:999px;font-weight:800;border:1px solid #444;
  background:#111;color:#eee;
}
.status-pick.st-novo,.status-pick.st-atendendo,.status-pick.st-pedido_enviado,.status-pick.st-comprar_depois,.status-pick.st-confirmado,.status-pick.st-recompra,.status-pick.st-pago,
.status-pick.st-entregue,.status-pick.st-devolvido,.status-pick.st-cancelado{
  /* herda as cores do status */
  border-color: inherit;
}

/* === STATUS COLORS UNIFIED === */
.st-novo{background:#2b2b2b;border:2px solid #444;color:#e0e0e0;}
.st-atendendo{background:#0e7490;border:2px solid #a5f3fc;color:#ecfeff;}
.st-pedido_enviado{background:#3730a3;border:2px solid #c7d2fe;color:#eef2ff;}
.st-confirmado{background:#6d28d9;border:2px solid #ddd6fe;color:#f5f3ff;}
.st-recompra{background:#be185d;border:2px solid #fbcfe8;color:#fdf2f8;}
.st-pago{background:#0f5c3b;border:2px solid #1f8a5f;color:#ffffff;}
.st-entregue{background:#124c3b;border:2px solid #1f7a60;color:#ffffff;}
.st-devolvido{background:#5a3b13;border:2px solid #8b5a1e;color:#fff3e0;}
.st-cancelado{background:#5a1a1a;border:2px solid #8b2a2a;color:#ffecec;}

select.status-pick{
  padding:6px 14px;
  border-radius:999px;
  font-weight:900;
  text-transform:lowercase;
  display:inline-flex;
  align-items:center;
  justify-content:center;
  border:2px solid #444;
  background:#111;
  color:#eee;
  cursor:pointer;
  appearance:auto;
}

select.status-pick.st-novo{background:#2b2b2b;border-color:#444;color:#e0e0e0;}
select.status-pick.st-atendendo{background:#0e7490;border-color:#a5f3fc;color:#ecfeff;}
select.status-pick.st-pedido_enviado{background:#3730a3;border-color:#c7d2fe;color:#eef2ff;}
select.status-pick.st-comprar_depois{background:#5f4b12;border-color:#b68a1f;color:#fff7d6;}
select.status-pick.st-confirmado{background:#6d28d9;border-color:#ddd6fe;color:#f5f3ff;}
select.status-pick.st-pago{background:#0f5c3b;border-color:#1f8a5f;color:#fff;}
select.status-pick.st-entregue{background:#124c3b;border-color:#1f7a60;color:#fff;}
select.status-pick.st-recompra{background:#be185d;border-color:#fbcfe8;color:#fdf2f8;}
select.status-pick.st-devolvido{background:#5a3b13;border-color:#8b5a1e;color:#fff3e0;}
select.status-pick.st-cancelado{background:#5a1a1a;border-color:#8b2a2a;color:#ffecec;}


/* === STATUS_COLOR_AUTOSAVE === */
select.status-pick{
  -webkit-appearance:none;
  appearance:none;
  padding:6px 28px 6px 10px;
  border-radius:10px;
  border:1px solid rgba(255,255,255,.18);
  font-weight:700;
  cursor:pointer;
}
select.status-pick:disabled{ opacity:.65; cursor:wait; }
select.status-pick.st-novo      { background:#2f2f2f; color:#fff; }
select.status-pick.st-atendendo { background:#0e7490; color:#ecfeff; }
select.status-pick.st-pedido_enviado { background:#3730a3; color:#eef2ff; }
select.status-pick.st-comprar_depois { background:#8a6d1d; color:#fff; }
select.status-pick.st-confirmado{ background:#6d28d9; color:#f5f3ff; }
select.status-pick.st-entregue  { background:#0f6b3b; color:#fff; }
select.status-pick.st-recompra  { background:#be185d; color:#fdf2f8; }
select.status-pick.st-devolvido { background:#b26a00; color:#fff; }
select.status-pick.st-cancelado { background:#b32020; color:#fff; }
select.status-pick.st-pago      { background:#6a2fb8; color:#fff; }

.badge-saved{
  display:inline-block;
  margin-left:8px;
  font-size:12px;
  opacity:.85;
}

/* === CODEX_STATUS_PALETTE_20260524 === */
.st-atendendo{background:#0e7490!important;border-color:#a5f3fc!important;color:#ecfeff!important}
.st-pedido_enviado{background:#3730a3!important;border-color:#c7d2fe!important;color:#eef2ff!important}
.st-confirmado{background:#6d28d9!important;border-color:#ddd6fe!important;color:#f5f3ff!important}
.st-recompra{background:#be185d!important;border-color:#fbcfe8!important;color:#fdf2f8!important}
.st-devolvido{background:#9a3412!important;border-color:#fed7aa!important;color:#fff7ed!important}
</style>
<link rel="stylesheet" href="/static/status.css">
</head>
<body>
<div class="wrap"><div class="card">
<h2>Admin</h2>
<form method="post">
<label>Usuário</label><input name="user">
<label>Senha</label><input name="pass" type="password">
<button>Entrar</button>
{% if err %}<div class="err">{{err}}</div>{% endif %}
</form>
</div></div>

<!-- CO_QTY_VALUE_SYNC_JS -->
<script>
document.addEventListener("DOMContentLoaded", function () {
  const qtyEl = document.querySelector('select[name="product_qty"]');
  const valEl = document.querySelector('input[name="product_value"]');
  const countryEl = document.querySelector('input[name="country"], select[name="country"]');
  if (!qtyEl || !valEl || !countryEl) return;

  const CO_PRICE_MAP = { "1":170000, "2":300000, "3":350000, "6":584000 };

  function cc(v) { return (v || "").toString().trim().toUpperCase(); }

  function setVal(x) {
    valEl.value = String(x);
    valEl.setAttribute("value", String(x)); // reforço visual
    try { valEl.dispatchEvent(new Event("input", { bubbles: true })); } catch(e){}
    try { valEl.dispatchEvent(new Event("change", { bubbles: true })); } catch(e){}
  }

  function applyPrice() {
    const country = cc(countryEl.value);
    const qty = String(parseInt(qtyEl.value || "1", 10));
    if (country === "CO") {
      const p = CO_PRICE_MAP[qty];
      if (p !== undefined) setVal(Number(p));
    }
  }

  qtyEl.addEventListener("change", applyPrice);
  countryEl.addEventListener("change", applyPrice);
  applyPrice();
});
</script>

<script src="/static/status.js"></script>

<script>
// Status autosave fica concentrado no script principal acima.
</script>

</body></html>
"""



@app.route("/admin", methods=["GET"])
def admin_selector():
    return redirect("/admin/dashboard", code=302)

def _admin_dashboard_rows(country_filter="ALL"):
    import os as _os
    import sqlite3 as _sqlite3

    selected = (country_filter or "ALL").strip().upper()
    countries = ["EC", "CO"] if selected not in ("EC", "CO") else [selected]
    rows = []
    dashboard_limit = int(os.getenv("MVP_ADMIN_DASHBOARD_LIMIT", "2000") or "2000")
    dashboard_limit = max(50, min(dashboard_limit, 3000))
    sql = """
        SELECT id, name, phone, address, city, province, product_qty, product_value,
               status, COALESCE(country, ?) AS country, COALESCE(created_at, '') AS panel_date,
               COALESCE(buy_later_followup_at, '') AS buy_later_followup_at
        FROM leads
        ORDER BY COALESCE(created_at, '') DESC, id DESC
        LIMIT ?
    """

    for country in countries:
        db_path = _DB_BY_COUNTRY.get(country)
        if not db_path or not _os.path.exists(db_path):
            continue
        conn = _sqlite3.connect(db_path)
        try:
            ensure_leads_tracking_columns(conn)
            cur = conn.cursor()
            cur.execute(sql, (country, dashboard_limit))
            for row in cur.fetchall():
                fixed = list(row)
                fixed[9] = (fixed[9] or country or "").upper()
                rows.append(tuple(fixed))
        finally:
            conn.close()

    def _sort_key(row):
        return str(row[10] or ""), int(row[0] or 0)

    rows.sort(key=_sort_key, reverse=True)
    return rows[:dashboard_limit]

def _normalize_admin_search(value):
    import unicodedata as _unicodedata
    normalized = _unicodedata.normalize("NFD", safe_str(value))
    normalized = "".join(ch for ch in normalized if _unicodedata.category(ch) != "Mn")
    return normalized.lower().strip()

def _admin_filter_rows(rows, query):
    q = _normalize_admin_search(query)
    if not q:
        return rows
    compact_q = "".join(ch for ch in q if ch.isdigit())
    filtered = []
    for row in rows:
        haystack = _normalize_admin_search(" ".join(str(value or "") for value in row))
        compact_haystack = "".join(ch for ch in haystack if ch.isdigit())
        if q in haystack or (compact_q and compact_q in compact_haystack):
            filtered.append(row)
    return filtered

def _admin_confirmed_today_rows(country):
    import os as _os
    import sqlite3 as _sqlite3

    selected = (country or "EC").strip().upper()
    db_path = _DB_BY_COUNTRY.get(selected)
    if not db_path or not _os.path.exists(db_path):
        return []

    confirmed_limit = int(os.getenv("MVP_ADMIN_CONFIRMED_LIMIT", "260") or "260")
    confirmed_limit = max(50, min(confirmed_limit, 800))
    sql = """
        WITH latest_confirmed AS (
            SELECT lead_id, MAX(created_at) AS confirmed_at
            FROM lead_status_history
            WHERE lower(coalesce(new_status,'')) = 'confirmado'
            GROUP BY lead_id
        )
        SELECT id, name, phone, address, city, province, product_qty, product_value,
               status, COALESCE(country, ?) AS country, COALESCE(latest_confirmed.confirmed_at, updated_at, created_at, '') AS confirmed_date,
               COALESCE(buy_later_followup_at, '') AS buy_later_followup_at
        FROM leads
        LEFT JOIN latest_confirmed ON latest_confirmed.lead_id = leads.id
        WHERE status = 'confirmado' COLLATE NOCASE
        ORDER BY COALESCE(latest_confirmed.confirmed_at, updated_at, created_at, '') DESC, id DESC
        LIMIT ?
    """
    conn = _sqlite3.connect(db_path)
    try:
        ensure_leads_tracking_columns(conn)
        ensure_master_history_tables(conn)
        cur = conn.cursor()
        cur.execute(sql, (selected, confirmed_limit))
        rows = []
        for row in cur.fetchall():
            fixed = list(row)
            fixed[9] = (fixed[9] or selected or "").upper()
            rows.append(tuple(fixed))
        return rows
    finally:
        conn.close()

@app.route("/admin/dashboard")
def admin_dashboard():
    guard = admin_required()
    if guard: return guard
    country_filter = (request.args.get("country") or "ALL").strip().upper()
    search_query = safe_str(request.args.get("q") or "")
    rows = _admin_filter_rows(_admin_dashboard_rows(country_filter), search_query)
    confirmed_today = {
        "EC": _admin_confirmed_today_rows("EC"),
        "CO": _admin_confirmed_today_rows("CO"),
    }
    return render_template_string(DASHBOARD_HTML, rows=rows, confirmed_today=confirmed_today, fmt_created_at_display=fmt_created_at_display, format_buy_later_followup=format_buy_later_followup, status_class=status_class, statuses=STATUSES, search_query=search_query, country_filter=country_filter)


# =========================
# EDIT (COM PREÇO AUTOMÁTICO)
# =========================
EDIT_HTML = """
<!doctype html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Editar Lead</title>
<style>
body{font-family:Arial;background:#0b0b0b;color:#fff}
.wrap{max-width:760px;margin:0 auto;padding:18px}
.card{border:1px solid #333;border-radius:14px;background:#111;padding:16px}
label{display:block;margin:10px 0 6px;font-weight:700}
input,select,textarea{width:100%;padding:10px;border-radius:10px;border:1px solid #333;background:#0b0b0b;color:#fff}
textarea{min-height:90px}
.row{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.actions{margin-top:14px;display:flex;gap:10px}
button{padding:12px 16px;border-radius:12px;border:0;background:#25d366;color:#000;font-weight:900}
a.btn{padding:12px 16px;border-radius:12px;border:1px solid #333;color:#fff;text-decoration:none}
</style>
</head>
<body>
<div class="wrap"><div class="card">
<h2>Editar Lead #{{lead.id}}</h2>
<form method="post" onsubmit="return confirm('Deseja alterar os dados/status deste cliente em todos os paineis?');">
<label>Nome</label><input name="name" value="{{lead.name}}">
<div class="row">
<div><label>Telefone</label><input name="phone" value="{{lead.phone}}"></div>
<div><label>Status</label>
<select name="status" class="status-pick" data-lead-id="{{ lead.id }}">
{% if lead.status not in statuses %}<option value="{{lead.status}}" selected>{{lead.status.replace('_', ' ')}} antigo</option>{% endif %}
{% for s in statuses %}<option value="{{s}}" {% if lead.status==s %}selected{% endif %}>{{s.replace('_', ' ')}}</option>{% endfor %}
</select></div>
</div>
<div class="row">
<div><label>Cidade</label><input name="city" value="{{lead.city}}"></div>
<div><label>Província</label><input name="province" value="{{lead.province}}"></div>
</div>
<label>Endereço</label><textarea name="address">{{lead.address}}</textarea>
<label>Data para comprar depois</label><input name="buy_later_followup_at" value="{{ format_buy_later_followup(lead.buy_later_followup_at or '') }}" placeholder="25-05-2026 09:00">
<div class="row">
<div><label>Quantidade</label>

<select name="product_qty" id="product_qty">
    <option value="1" {{ 'selected' if (lead.product_qty|string) == '1' else '' }}>1</option>
  <option value="2" {{ 'selected' if (lead.product_qty|string) == '2' else '' }}>2</option>
  <option value="3" {{ 'selected' if (lead.product_qty|string) == '3' else '' }}>3</option>
  <option value="6" {{ 'selected' if (lead.product_qty|string) == '6' else '' }}>6</option>

</select>
</div>
<div><label>Valor</label><input name="product_value" id="product_value" value="{{lead.product_value}}" readonly></div>
</div>
<label>País</label><input name="country" value="{{lead.country}}">
<div class="actions">
<button>Salvar</button><a class="btn" href="/admin/dashboard">⬅ Voltar</a>
</div>
</form>
</div></div>
<script>
(function(){
  const PRICE_TABLE = {
    EC: { "1": 39.99, "2": 69.99, "3": 95.99, "6": 167.99 },
    CO: { "1": 170000, "2": 300000, "3": 350000, "6": 584000 }
  };

  function formatPrice(country, price){
    if (price == null) return "";
    const c = String(country || "EC").toUpperCase().trim();
    if (c === "CO") return String(Math.round(Number(price)));
    return Number(price).toFixed(2);
  }

  function update(){
    const qtyEl = document.getElementById("product_qty") || document.querySelector('select[name="product_qty"]');
    const countryEl = document.querySelector('input[name="country"]');
    const vEl = document.getElementById("product_value") || document.querySelector('input[name="product_value"]');
    if (!qtyEl || !countryEl || !vEl) return;

    const c = String(countryEl.value || "EC").toUpperCase().trim();
    const q = String(qtyEl.value || "").trim();
    const price = (PRICE_TABLE[c] || {})[q];
    if (typeof price === "number") vEl.value = formatPrice(c, price);
  }

  document.addEventListener("DOMContentLoaded", update);
  document.addEventListener("change", function(e){
    if (e && e.target && (e.target.id === "product_qty" || e.target.name === "product_qty")) update();
  });
})();
</script>
</body></html>
"""



@app.route("/admin/new", methods=["GET","POST"])
def admin_new_lead():
    # Reusa exatamente o mesmo formulário de edição, mas com lead_id=0 (novo)
    return admin_edit_lead(0)

@app.route("/admin/edit/<int:lead_id>", methods=["GET","POST"])
def admin_edit_lead(lead_id):
    guard = admin_required()
    if guard:
        return guard

    admin_country = safe_str(request.values.get("country")).strip().upper()
    db_path = _DB_BY_COUNTRY.get(admin_country)
    if db_path:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    else:
        conn = db_conn()
    cur = conn.cursor()

    if request.method == "POST":

        # ===== NOVO: criar lead quando lead_id == 0 (reusa o mesmo formulário) =====
        if lead_id == 0:
            name = safe_str(request.form.get("name"))
            country = safe_str(request.form.get("country"))
            target_country = (country or admin_country or "EC").strip().upper()
            phone_norm = normalize_phone_by_country(request.form.get("phone"), target_country)
            city = safe_str(request.form.get("city"))
            province = safe_str(request.form.get("province"))
            address = safe_str(request.form.get("address"))
            product_qty = safe_str(request.form.get("product_qty"))
            product_value = safe_str(request.form.get("product_value"))
            status = safe_str(request.form.get("status") or "novo") or "novo"
            # AUTO-PREÇO CO (COP) por quantidade
            if target_country == "CO":
                pv = _co_price_for_qty(product_qty)
                if pv is not None:
                    product_value = str(pv)
            source = "manual"

            if not name or not phone_norm:
                flash("Nome e telefone são obrigatórios para incluir cliente.", "error")
                return redirect(url_for("admin_new_lead"))

            if is_blocked_operational_phone(phone_norm, target_country):
                flash("Telefone operacional/atendente bloqueado para cadastro no painel.", "error")
                return redirect(url_for("admin_new_lead", country=target_country))

            target_db_path = _DB_BY_COUNTRY.get(target_country) or db_path
            dup_id = find_existing_lead_id_by_phone(phone_norm, target_country, db_path=target_db_path)
            if dup_id:
                flash("Este telefone já está cadastrado. Abra o cadastro existente para editar.", "warning")
                return redirect(url_for("admin_edit_lead", lead_id=int(dup_id), country=target_country))

            conn2 = sqlite3.connect(target_db_path) if target_db_path else db_conn()
            cur2 = conn2.cursor()
            ensure_leads_tracking_columns(conn2)
            ensure_master_history_tables(conn2)

            new_id = None
            try:
                ts = now_iso_utc()
                cur2.execute(
                    "INSERT INTO leads (name, phone, city, province, address, product_qty, product_value, status, country, created_at, source) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (name, phone_norm, city, province, address, product_qty, product_value, status, target_country, ts, source),
                )
                new_id = cur2.lastrowid
                record_status_snapshot(cur2, new_id, status, "admin_manual_created", ts)
                conn2.commit()
                conn2.close()
            except Exception:
                conn2.rollback()
                try:
                    cur2.execute(
                        "INSERT INTO leads (name, phone, city, province, address, product_qty, product_value, status, country, source) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (name, phone_norm, city, province, address, product_qty, product_value, status, target_country, source),
                    )
                    new_id = cur2.lastrowid
                    record_status_snapshot(cur2, new_id, status, "admin_manual_created", now_iso_utc())
                    conn2.commit()
                    conn2.close()
                except Exception as e2:
                    conn2.rollback()
                    conn2.close()
                    flash(f"Falha ao incluir cliente: {e2}", "error")
                    return redirect(url_for("admin_new_lead"))

            flash("Cliente incluído com sucesso.", "success")
            return redirect(url_for("admin_edit_lead", lead_id=int(new_id), country=target_country))
        # ===== FIM NOVO =====
        # Captura status anterior (para detectar transição -> confirmado)
        prev_status = ""
        prev_lead_row = {}
        try:
            ensure_leads_tracking_columns(conn)
            cur2 = conn.cursor()
            cur2.execute("SELECT * FROM leads WHERE id=?", (lead_id,))
            r0 = cur2.fetchone()
            if r0:
                prev_lead_row = row_to_dict(r0)
                prev_status = (prev_lead_row.get("status") or "")
        except Exception:
            prev_status = ""

        # Novos valores (normalizados)
        name = safe_str(request.form.get("name"))
        country = safe_str(request.form.get("country"))
        target_country = (country or admin_country or "EC").strip().upper()
        phone_norm = normalize_phone_by_country(request.form.get("phone"), target_country)
        city = safe_str(request.form.get("city"))
        province = safe_str(request.form.get("province"))
        address = safe_str(request.form.get("address"))
        product_qty = safe_str(request.form.get("product_qty"))
        product_value = safe_str(request.form.get("product_value"))
        new_status = safe_str(request.form.get("status"))
        buy_later_followup_at = safe_str(request.form.get("buy_later_followup_at"))

        if is_blocked_operational_phone(phone_norm, target_country):
            flash("Telefone operacional/atendente bloqueado para cadastro no painel.", "error")
            return redirect(url_for("admin_edit_lead", lead_id=int(lead_id), country=target_country))
        dup_id = find_existing_lead_id_by_phone(phone_norm, target_country, db_path=db_path, exclude_id=lead_id)
        if dup_id:
            flash("Este telefone já está cadastrado em outro cliente. Abra o cadastro existente para editar.", "warning")
            return redirect(url_for("admin_edit_lead", lead_id=int(dup_id), country=target_country))

        if new_status == "comprar_depois" and not buy_later_followup_at:
            buy_later_followup_at = default_buy_later_followup_at()
        if new_status == "comprar_depois":
            buy_later_followup_at = normalize_buy_later_followup_at(buy_later_followup_at)
            if not buy_later_followup_at:
                flash("Data de comprar depois invalida. Use 25-05-2026 09:00.", "error")
                return redirect(url_for("admin_edit_lead", lead_id=int(lead_id), country=target_country))
        if new_status == "recompra" and (prev_status or "").strip().lower() != "entregue":
            flash("Recompra so pode ser marcada para cliente com pedido entregue.", "error")
            return redirect(url_for("admin_edit_lead", lead_id=int(lead_id), country=target_country))
        blocked, block_reason = status_change_blocked(prev_status, new_status)
        if blocked:
            flash("Status protegido: pedido enviado/entregue/cancelado/devolvido nao volta sozinho para confirmado ou funil.", "error")
            return redirect(url_for("admin_edit_lead", lead_id=int(lead_id), country=target_country))
        cur.execute(
            "UPDATE leads SET name=?, phone=?, city=?, province=?, address=?, product_qty=?, product_value=?, status=?, country=?, buy_later_followup_at=?, buy_later_notified_at=? WHERE id=?",
            (
                name,
                phone_norm,
                city,
                province,
                address,
                product_qty,
                product_value,
                new_status,
                target_country,
                buy_later_followup_at if new_status == "comprar_depois" else "",
                None,
                lead_id
            ),
        )
        record_status_change(cur, lead_id, prev_status, new_status, "admin_edit")
        conn.commit()
        mirror_status_to_whatsapp_panel({
            "id": lead_id,
            "status": new_status,
            "old_status": prev_status,
            "phone": phone_norm,
            "phone_e164": phone_norm,
            "name": name,
            "country": target_country,
            "buy_later_followup_at": buy_later_followup_at if new_status == "comprar_depois" else "",
        })

        # Gatilho META (TREINO): Purchase quando status vira "confirmado"
        try:
            if (prev_status or "").lower() != "confirmado" and (new_status or "").lower() == "confirmado":
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS purchase_capi_lock (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        lead_id INTEGER NOT NULL UNIQUE,
                        phone TEXT,
                        status TEXT NOT NULL,
                        created_at TEXT
                    )
                """)
                cur.execute("SELECT 1 FROM purchase_capi_lock WHERE lead_id=?", (lead_id,))
                if not cur.fetchone():
                    lead_for_purchase = dict(prev_lead_row)
                    lead_for_purchase.update({
                        "id": lead_id,
                        "phone": phone_norm,
                        "product_value": product_value,
                        "country": country,
                    })
                    event_id = f"purchase_confirmado_lead_{country or 'EC'}_{lead_id}"
                    capi = send_meta_capi_purchase_for_lead(lead_for_purchase, event_id=event_id, country=(country or None))
                    if capi and capi.get("ok"):
                        cur.execute(
                            "INSERT OR IGNORE INTO purchase_capi_lock (lead_id, phone, status, created_at) VALUES (?, ?, ?, ?)",
                            (lead_id, phone_norm, new_status, now_iso_utc()),
                        )
                        conn.commit()
        except Exception as e:
            try:
                app.logger.exception(f"FB_CAPI_PURCHASE_ERROR lead={lead_id} error={e}")
            except Exception:
                pass

        conn.close()
        return redirect("/admin/dashboard" + (("?country=" + admin_country) if admin_country in ("EC", "CO") else ""))

    # GET
    cur.execute("SELECT * FROM leads WHERE id=?", (lead_id,))
    lead = cur.fetchone()
    conn.close()
#     __FIX_NEW_GET_ZERO_V2__
    if not lead:
        # Se for "novo" (lead_id==0), renderiza o mesmo formulário com lead vazio
        if lead_id == 0:
            lead = SimpleNamespace(
                id=0,
                name="",
                phone="",
                city="",
                province="",
                address="",
                product_qty="1",
                product_value="",
                status="novo",
                country="",
            )
            return render_template_string(EDIT_HTML, lead=lead, statuses=STATUSES, r=lead, format_buy_later_followup=format_buy_later_followup)
        abort(404)
    return render_template_string(EDIT_HTML, lead=lead, statuses=STATUSES, r=lead, format_buy_later_followup=format_buy_later_followup)
# =========================
# EXPORT CSV
# =========================
@app.route("/admin/export.csv")
def admin_export_csv():
    guard = admin_required()
    if guard: return guard
    conn=db_conn(); cur=conn.cursor()
    cur.execute("SELECT * FROM leads ORDER BY id DESC")
    rows=cur.fetchall(); conn.close()
    out=StringIO(); w=csv.writer(out)
    w.writerow(["id","name","phone","city","province","address","product_qty","product_value","status","country","created_at"])
    for r in rows:
        w.writerow([r[k] for k in r.keys()])
    return Response(out.getvalue(), mimetype="text/csv", headers={"Content-Disposition":"attachment; filename=leads.csv"})


@app.route("/admin/delete/<int:lead_id>", methods=["GET","POST"])
def admin_delete_lead(lead_id):
    guard = admin_required()
    if guard: return guard

    admin_country = safe_str(request.values.get("country")).strip().upper()
    db_path = _DB_BY_COUNTRY.get(admin_country)
    if db_path:
        conn = sqlite3.connect(db_path)
    else:
        conn = db_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()
    return redirect("/admin/dashboard" + (("?country=" + admin_country) if admin_country in ("EC", "CO") else ""))



### ADMIN_TREINO_ALIASES ###
# from flask import Flask, request, session, redirect, url_for, render_template, render_template_string


# === ADMIN LOGIN HTML (PROD - FIX) ===
ADMIN_LOGIN_HTML = """
<!doctype html><html><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Admin Login</title>
<style>
body{font-family:Arial,sans-serif;background:#0b0b0b;color:#fff;margin:0;padding:24px}
.card{max-width:420px;margin:40px auto;background:#141414;border:1px solid #222;border-radius:12px;padding:18px}
input{width:100%;padding:12px;border-radius:10px;border:1px solid #333;background:#0f0f0f;color:#fff;margin:8px 0}
button{width:100%;padding:12px;border-radius:10px;border:0;background:#d4af37;color:#000;font-weight:700;margin-top:10px}
</style></head><body>
<div class="card"><h2>Admin</h2>
<form method="post" action="/admin/login">
  <input name="user" placeholder="Usuário" required />
  <input name="pass" placeholder="Senha" type="password" required />
  <input name="next" type="hidden" value="{{ next_url }}" />
  <button type="submit">Entrar</button>
</form></div></body></html>
"""
# === /ADMIN LOGIN HTML (PROD - FIX) ===




def admin_login():
    next_url = (request.values.get("next") or "/admin/dashboard").strip()
    if not next_url.startswith("/admin/"):
        next_url = "/admin/dashboard"
    if request.method == "POST":
        user = request.form.get("user", "")
        password = request.form.get("pass", "")
        valid = False
        try:
            valid = bool(ADMIN_PASS_HASH) and user == ADMIN_USER and check_password_hash(ADMIN_PASS_HASH, password or "")
        except Exception:
            valid = False
        if valid:
            session.clear()
            session["admin_ok"] = True
            session["admin_env"] = "PROD"
            return redirect(next_url)
        try:
            flash("Usuario ou senha invalidos", "error")
        except Exception:
            pass
    return render_template_string(ADMIN_LOGIN_HTML, next_url=next_url)

def admin_logout():
    session.clear()
    return redirect("/admin/login")

def _rewrite_admin_location(resp):
    """Reescreve redirects /admin/... -> /treino/admin/... sem quebrar.

    Esta função é usada pelas rotas /treino/admin/* para reescrever Location em redirects.
    Nunca pode levantar NameError.
    """
    try:
        loc = resp.headers.get("Location")
        if loc and loc.startswith("/admin/"):
            resp.headers["Location"] = loc.replace("/admin/", "/treino/admin/", 1)
    except Exception:
        pass

    # Opcional: reescrever corpo HTML de redirects simples (link no corpo)
    try:
        ctype = (getattr(resp, "mimetype", "") or "")
        if ctype == "text/html":
            body = resp.get_data(as_text=True)
            if "/admin/" in body:

                # Corrige também o <form action> do login quando vem do /treino
                body = body.replace('action="/admin/login"', 'action="/treino/admin/login"')
                body = body.replace("action='/admin/login'", "action='/treino/admin/login'")
                body = body.replace('href="/admin/', 'href="/treino/admin/')
                body = body.replace('>/admin/', '>/treino/admin/')
                resp.set_data(body)
    except Exception:
        pass

    return resp




def _admin_cors_origin():
    origin = request.headers.get("Origin") or ""
    allowed = {
        "https://painel.maxlien.shop",
        "http://painel.maxlien.shop",
        "null",
    }
    return origin if origin in allowed else ""

def _admin_json_response(payload, status=200):
    resp = jsonify(payload)
    resp.status_code = status
    origin = _admin_cors_origin()
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Vary"] = "Origin"
    return resp

def _admin_cors_preflight():
    resp = make_response("", 204)
    origin = _admin_cors_origin()
    if origin:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Access-Control-Allow-Credentials"] = "true"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
        resp.headers["Vary"] = "Origin"
    return resp

@app.route("/admin/api/leads", methods=["GET", "OPTIONS"])
def admin_api_leads():
    if request.method == "OPTIONS":
        return _admin_cors_preflight()

    guard = admin_required()
    if guard:
        return _admin_json_response({"ok": False, "error": "auth_required", "login_url": "https://maxlien.shop/admin/login"}, 401)

    country_filter = (request.args.get("country") or "ALL").strip().upper()
    limit = min(max(int(request.args.get("limit") or 500), 1), 3000)
    search_query = safe_str(request.args.get("q") or "")
    rows = _admin_filter_rows(_admin_dashboard_rows(country_filter), search_query)[:limit]
    leads = []

    for row in rows:
        try:
            value = float(row[7] or 0)
        except Exception:
            value = 0.0
        leads.append({
            "id": row[0],
            "name": row[1] or "",
            "phone": row[2] or "",
            "address": row[3] or "",
            "city": row[4] or "",
            "province": row[5] or "",
            "quantity": row[6] or "",
            "value": value,
            "status": row[8] or "novo",
            "country": (row[9] or "EC").upper(),
            "created_at": row[10] or "",
            "created_at_display": fmt_created_at_display(row[10] or ""),
            "buy_later_followup_at": row[11] if len(row) > 11 else "",
            "buy_later_followup_display": format_buy_later_followup(row[11] if len(row) > 11 else ""),
        })

    return _admin_json_response({"ok": True, "leads": leads, "statuses": STATUSES})


@app.route("/admin/api/status", methods=["POST", "OPTIONS"])
def admin_api_status():
    if request.method == "OPTIONS":
        return _admin_cors_preflight()
    guard = admin_required()
    if guard:
        return _admin_json_response({"ok": False, "error": "unauthorized", "login_url": "https://maxlien.shop/admin/login"}, 401)

    data = request.get_json(silent=True) or {}
    try:
        lead_id = int(data.get("id") or 0)
    except Exception:
        lead_id = 0
    new_status = safe_str(data.get("status")).strip().lower()
    followup_at = safe_str(data.get("followup_at") or data.get("buy_later_followup_at")).strip()

    if not lead_id:
        return _admin_json_response({"ok": False, "error": "lead_id_required"}, 400)
    if new_status not in set(STATUSES):
        return _admin_json_response({"ok": False, "error": "invalid_status"}, 400)

    status_country = safe_str(data.get("country")).strip().upper()
    db_path = _DB_BY_COUNTRY.get(status_country)
    if db_path:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
    else:
        conn = db_conn()
    cur = conn.cursor()
    ensure_leads_tracking_columns(conn)
    cur.execute("""
        SELECT id, name, status, phone, phone_e164, product_value, country, event_id,
               fbp, fbc, fbclid, client_ip_address, client_user_agent, event_source_url
        FROM leads WHERE id=?
    """, (lead_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return _admin_json_response({"ok": False, "error": "lead_not_found"}, 404)

    old_status = (row["status"] or "")
    old_status_normalized = (old_status or "").strip().lower()
    if new_status == "recompra" and old_status_normalized != "entregue":
        conn.close()
        return _admin_json_response({"ok": False, "error": "recompra_somente_para_entregue"}, 400)
    blocked, block_reason = status_change_blocked(old_status, new_status)
    if blocked:
        conn.close()
        return _admin_json_response({"ok": False, "error": block_reason}, 409)
    if new_status == "comprar_depois":
        if not followup_at:
            conn.close()
            return _admin_json_response({"ok": False, "error": "followup_at_required"}, 400)
        followup_at = normalize_buy_later_followup_at(followup_at)
        if not followup_at:
            conn.close()
            return _admin_json_response({"ok": False, "error": "invalid_followup_at"}, 400)
        cur.execute(
            "UPDATE leads SET status=?, buy_later_followup_at=?, buy_later_notified_at=NULL WHERE id=?",
            (new_status, followup_at, lead_id)
        )
    elif old_status_normalized == "comprar_depois":
        cur.execute(
            "UPDATE leads SET status=?, buy_later_followup_at='', buy_later_notified_at=NULL WHERE id=?",
            (new_status, lead_id)
        )
    else:
        cur.execute("UPDATE leads SET status=? WHERE id=?", (new_status, lead_id))
    record_status_change(cur, lead_id, old_status, new_status, "admin_api_status")

    notification_statuses = {"pedido_enviado", "entregue", "cancelado", "devolvido"}
    notification_allowed = False
    notification_duplicate = False
    if new_status in notification_statuses and (old_status or "").strip().lower() != new_status:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS status_notification_lock (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL,
                phone TEXT,
                status TEXT NOT NULL,
                created_at TEXT,
                UNIQUE(lead_id, status)
            )
        """)
        try:
            cur.execute(
                "INSERT INTO status_notification_lock (lead_id, phone, status, created_at) VALUES (?, ?, ?, ?)",
                (lead_id, row["phone"], new_status, now_iso_utc()),
            )
            notification_allowed = True
        except Exception:
            notification_duplicate = True

    purchase_allowed = False
    purchase_duplicate = False
    should_send_purchase = _should_fire_purchase(old_status, new_status)
    if should_send_purchase:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS purchase_capi_lock (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                lead_id INTEGER NOT NULL UNIQUE,
                phone TEXT,
                status TEXT NOT NULL,
                created_at TEXT
            )
        """)
        cur.execute("SELECT 1 FROM purchase_capi_lock WHERE lead_id=?", (lead_id,))
        if cur.fetchone():
            purchase_duplicate = True
        else:
            purchase_allowed = True

    conn.commit()
    conn.close()

    mirror_result = mirror_status_to_whatsapp_panel({
        "id": lead_id,
        "status": new_status,
        "old_status": old_status,
        "phone": row["phone"],
        "phone_e164": row["phone_e164"],
        "name": row["name"],
        "country": status_country or row["country"] or "EC",
        "buy_later_followup_at": followup_at if new_status == "comprar_depois" else "",
    })

    capi = None
    try:
        if purchase_allowed:
            event_id = f"purchase_confirmado_lead_{status_country or 'EC'}_{lead_id}"
            capi = send_meta_capi_purchase_for_lead(row, event_id=event_id, country=(status_country or None))
            if capi and capi.get("ok"):
                try:
                    lock_conn = sqlite3.connect(db_path or DB_PATH)
                    lock_cur = lock_conn.cursor()
                    lock_cur.execute("""
                        CREATE TABLE IF NOT EXISTS purchase_capi_lock (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            lead_id INTEGER NOT NULL UNIQUE,
                            phone TEXT,
                            status TEXT NOT NULL,
                            created_at TEXT
                        )
                    """)
                    lock_cur.execute(
                        "INSERT OR IGNORE INTO purchase_capi_lock (lead_id, phone, status, created_at) VALUES (?, ?, ?, ?)",
                        (lead_id, row["phone"], new_status, now_iso_utc()),
                    )
                    lock_conn.commit()
                    lock_conn.close()
                except Exception:
                    try:
                        app.logger.exception(f"Erro ao gravar lock Purchase lead {lead_id}")
                    except Exception:
                        pass
            else:
                purchase_allowed = False
    except Exception as e:
        purchase_allowed = False
        try:
            app.logger.exception(f"Erro Meta Purchase status ajax lead {lead_id}: {e}")
        except Exception:
            pass

    return _admin_json_response({
        "ok": True,
        "id": lead_id,
        "old_status": old_status,
        "status": new_status,
        "capi": capi,
        "purchase_allowed": purchase_allowed,
        "purchase_duplicate": purchase_duplicate,
        "notification_allowed": notification_allowed,
        "notification_duplicate": notification_duplicate,
        "buy_later_followup_at": followup_at if new_status == "comprar_depois" else "",
        "buy_later_followup_display": format_buy_later_followup(followup_at) if new_status == "comprar_depois" else "",
        "whatsapp_panel_sync": mirror_result,
    })


### ADMIN_STATUS_QUICK_BEGIN ###
@app.route("/admin/status/<int:lead_id>", methods=["POST"])
def admin_quick_status(lead_id):
    guard = admin_required()
    if guard:
        return guard

    new_status = (request.form.get("status") or "").strip()
    followup_at = (request.form.get("buy_later_followup_at") or "").strip()
    allowed = set(STATUSES)
    if new_status and new_status not in allowed:
        return "Status inválido", 400

    conn = db_conn()
    ensure_leads_tracking_columns(conn)
    cur = conn.cursor()
    row = cur.execute("SELECT id, name, status, phone, phone_e164, country FROM leads WHERE id=?", (lead_id,)).fetchone()
    old_status = row["status"] if row else ""
    if new_status == "recompra" and (old_status or "").strip().lower() != "entregue":
        conn.close()
        return "Recompra somente para cliente entregue", 400
    blocked, block_reason = status_change_blocked(old_status, new_status)
    if blocked:
        conn.close()
        return "Status protegido: pedido enviado/entregue/cancelado/devolvido nao volta para confirmado sozinho", 409
    if new_status == "comprar_depois" and not followup_at:
        followup_at = default_buy_later_followup_at()
    if new_status == "comprar_depois":
        followup_at = normalize_buy_later_followup_at(followup_at)
        if not followup_at:
            conn.close()
            return "Data inválida. Use 25-05-2026 09:00", 400
    # Atualiza somente o status
    if new_status == "comprar_depois":
        cur.execute("UPDATE leads SET status=?, buy_later_followup_at=?, buy_later_notified_at=NULL WHERE id=?", (new_status, followup_at, lead_id))
    elif (old_status or "").strip().lower() == "comprar_depois":
        cur.execute("UPDATE leads SET status=?, buy_later_followup_at='', buy_later_notified_at=NULL WHERE id=?", (new_status, lead_id))
    else:
        cur.execute("UPDATE leads SET status=? WHERE id=?", (new_status, lead_id))
    record_status_change(cur, lead_id, old_status, new_status, "admin_quick_status")
    conn.commit()
    conn.close()
    if row:
        mirror_status_to_whatsapp_panel({
            "id": lead_id,
            "status": new_status,
            "old_status": old_status,
            "phone": row["phone"],
            "phone_e164": row["phone_e164"],
            "name": row["name"],
            "country": row["country"] or "EC",
            "buy_later_followup_at": followup_at if new_status == "comprar_depois" else "",
        })

    # volta para o dashboard (mantém simples e rápido)
    return redirect("/admin/dashboard")

### ADMIN_STATUS_QUICK_END ###



@app.route("/admin/crm")
def admin_crm_redirect():
    return redirect("/admin/crm/", code=302)

@app.route("/admin/crm/")
def admin_crm_panel():
    guard = admin_required()
    if guard:
        return guard
    return send_from_directory("/opt/maxlien-mvp/crm_panel", "index.html")

@app.route("/admin/crm/<path:filename>")
def admin_crm_asset(filename):
    guard = admin_required()
    if guard:
        return guard
    return send_from_directory("/opt/maxlien-mvp/crm_panel", filename)

@app.route("/admin/login", methods=["GET","POST"])
def admin_login_route():
    return admin_login()

@app.route("/admin/logout")
def admin_logout_route():
    return admin_logout()

@app.route("/treino/admin/login", methods=["GET","POST"])
def admin_treino_login():
    if not ENABLE_TREINO:
        return ("Not Found", 404)
    return _rewrite_admin_location(make_response(admin_login()))

@app.route("/treino/admin/logout")
def admin_treino_logout():
    if not ENABLE_TREINO:
        return ("Not Found", 404)
    return _rewrite_admin_location(admin_logout())

@app.route("/treino/admin/dashboard")
def admin_treino_dashboard():
    if not ENABLE_TREINO:
        return ("Not Found", 404)
    return _rewrite_admin_location(make_response(admin_dashboard()))

@app.route("/treino/admin/new", methods=["GET","POST"])
def admin_treino_new():
    if not ENABLE_TREINO:
        return ("Not Found", 404)
    return _rewrite_admin_location(admin_new_lead())

@app.route("/treino/admin/edit/<int:lead_id>", methods=["GET","POST"])
def admin_treino_edit(lead_id):
    if not ENABLE_TREINO:
        return ("Not Found", 404)
    return _rewrite_admin_location(admin_edit_lead(lead_id))

@app.route("/treino/admin/export.csv")
def admin_treino_export_csv():
    if not ENABLE_TREINO:
        return ("Not Found", 404)
    return _rewrite_admin_location(admin_export_csv())

@app.route("/treino/admin/delete/<int:lead_id>", methods=["POST"])
def admin_treino_delete(lead_id):
    if not ENABLE_TREINO:
        return ("Not Found", 404)
    return _rewrite_admin_location(admin_delete_lead(lead_id))
### /ADMIN_TREINO_ALIASES ###



### ADMIN_TREINO_ALIASES_V2 ###
# def _rewrite_location(resp):
#     """Se alguma função redirecionar para /admin/..., reescreve para /admin-treino/... quando fizer sentido."""
#     try:
#         loc = resp.headers.get("Location")
#     except Exception:
#         return resp
#     if loc and loc.startswith("/admin/"):
#         resp.headers["Location"] = "/admin-treino" + loc[len("/admin"):]
#     return resp
# 
# 
# 
# (aliases duplicados removidos automaticamente)





# =========================
# DEBUG: listar rotas carregadas (TREINO)
# =========================
@app.route("/__routes")
def __routes_debug():
    try:
        rules = sorted([r.rule for r in app.url_map.iter_rules()])
        return "\n".join(rules), 200, {"Content-Type": "text/plain; charset=utf-8"}
    except Exception as e:
        return f"ERR: {e}", 500, {"Content-Type": "text/plain; charset=utf-8"}

# === MAXLIEN_PROD_CONFIRM_ROUTE ===
@app.route('/confirm/<int:lead_id>', methods=['GET'])
def confirm_lead_quick(lead_id):
    import os, sqlite3
    key = ((request.args.get('key') or request.headers.get('X-Confirm-Key') or '').strip())
    need = (os.getenv('MVP_CONFIRM_KEY','') or '').strip()
    if (not need) or (key != need):
        return ('forbidden', 403)

    conn = db_conn()  # usa override por request (g._db_path_override) se existir
    cur = conn.cursor()
    ensure_leads_tracking_columns(conn)
    cur.execute('SELECT * FROM leads WHERE id=?', (int(lead_id),))
    row = cur.fetchone()
    if not row:
        conn.close()
        return ('not_found', 404)

    old_status = row["status"]
    new_status = 'confirmado'
    blocked, block_reason = status_change_blocked(old_status, new_status)
    if blocked:
        conn.close()
        return ('status_protegido', 409)

    cur.execute('UPDATE leads SET status=? WHERE id=?', (new_status, int(lead_id)))
    record_status_change(cur, lead_id, old_status, new_status, "admin_confirm_route")
    conn.commit()

    try:
        if _should_fire_purchase(old_status, new_status):
            cur.execute("""
                CREATE TABLE IF NOT EXISTS purchase_capi_lock (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER NOT NULL UNIQUE,
                    phone TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT
                )
            """)
            cur.execute("SELECT 1 FROM purchase_capi_lock WHERE lead_id=?", (int(lead_id),))
            if not cur.fetchone():
                lead_row = row_to_dict(row)
                event_id = f"purchase_confirmado_lead_{lead_row.get('country') or 'EC'}_{lead_id}"
                capi = send_meta_capi_purchase_for_lead(lead_row, event_id=event_id, country=lead_row.get("country"))
                if capi and capi.get("ok"):
                    cur.execute(
                        "INSERT OR IGNORE INTO purchase_capi_lock (lead_id, phone, status, created_at) VALUES (?, ?, ?, ?)",
                        (int(lead_id), lead_row.get("phone"), new_status, now_iso_utc()),
                    )
                    conn.commit()
    except Exception as e:
        try:
            app.logger.warning('FB_CAPI_PURCHASE_ERROR quick_confirm lead=%s error=%s', lead_id, e)
        except Exception:
            pass
    conn.close()

    return (f'ok lead={lead_id} prev={old_status} new={new_status}', 200)
# === /MAXLIEN_PROD_CONFIRM_ROUTE ===

# === HEALTHCHECK via /api/health (adicionado por patch) ===
@app.route("/api/health", methods=["GET"])
def api_health():
    return {"ok": True, "ts": int(time.time())}
# === AUTO: PEDIDO RAPIDO JSON APPEND (BEGIN) ===
import os
import json
import uuid
import sqlite3
from datetime import datetime
from flask import request, jsonify

def _pr_country_from_host(host: str) -> str:
    h = (host or "").lower()
    if "co.maxlien.shop" in h:
        return "CO"
    if "ec.maxlien.shop" in h:
        return "EC"
    return "EC"

def _pr_db_path_from_host(host: str) -> str:
    c = _pr_country_from_host(host)
    return "/opt/maxlien-mvp/leads_co.sqlite3" if c == "CO" else "/opt/maxlien-mvp/leads_ec.sqlite3"

def _pr_now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def _pr_digits(x: str) -> str:
    return "".join(ch for ch in (x or "") if ch.isdigit())

def _pr_to_e164(phone_raw: str, country: str) -> str:
    d = _pr_digits(phone_raw)
    if not d:
        return ""
    while d.startswith("0"):
        d = d[1:]
    cc = "593" if country == "EC" else ("57" if country == "CO" else "")
    if cc and d.startswith(cc):
        return "+" + d
    if cc:
        return "+" + cc + d
    return "+" + d

def _pr_send_smclick(payload: dict):
    url = (os.getenv("MVP_SMCLICK_WEBHOOK_URL") or "").strip()
    if not url:
        return False, "MVP_SMCLICK_WEBHOOK_URL não configurada (skip)"
    try:
        import urllib.request
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = getattr(resp, "status", 0)
            ok = 200 <= status < 300
            return ok, f"HTTP {status}"
    except Exception as e:
        return False, f"ERRO: {type(e).__name__}: {e}"

@app.route("/admin/pedido-rapido", methods=["GET", "POST"])
def admin_pedido_rapido():
    guard = admin_required()
    if guard:
        return guard

    host = request.host or ""
    data = request.get_json(silent=True) or {}
    if not data and request.form:
        data = dict(request.form)

    requested_country = (data.get("country") or data.get("pais") or request.values.get("country") or "").strip().upper()
    country = requested_country if requested_country in ("EC", "CO") else _pr_country_from_host(host)
    db_path = "/opt/maxlien-mvp/leads_co.sqlite3" if country == "CO" else "/opt/maxlien-mvp/leads_ec.sqlite3"

    if request.method == "GET":
        return jsonify({
            "ok": True,
            "route": "/admin/pedido-rapido",
            "host": host,
            "country": country,
            "db_path": db_path,
            "howto": "POST JSON ou form-data: name, phone, city, product_qty. province/address/notes opcionais."
        })

    name = (data.get("name") or "").strip()
    phone = (data.get("phone") or "").strip()
    city = (data.get("city") or "").strip()
    province = (data.get("province") or "").strip()
    address = (data.get("address") or "").strip()
    notes = (data.get("notes") or "").strip()
    product = (data.get("product") or "Maxlien").strip()

    try:
        qty = int(data.get("product_qty") or 1)
    except Exception:
        qty = 1
    if qty not in (1, 2, 3, 6):
        qty = 1

    backend_price = _price_for(country, str(qty))
    try:
        value = float(backend_price if backend_price is not None else (data.get("product_value") or 0.0))
    except Exception:
        value = 0.0

    if not name:
        return jsonify({"ok": False, "error": "name_required"}), 400
    if not phone:
        return jsonify({"ok": False, "error": "phone_required"}), 400

    event_id = uuid.uuid4().hex
    created_at = _pr_now_iso()
    updated_at = created_at
    phone_e164 = _pr_to_e164(phone, country)
    phone_norm = normalize_phone_by_country(phone, country) or phone_e164 or phone

    if is_blocked_operational_phone(phone_norm, country):
        return jsonify({"ok": False, "error": "operational_phone_blocked"}), 400

    dup_id = find_existing_lead_id_by_phone(phone_norm, country, db_path=db_path)
    if dup_id:
        return jsonify({
            "ok": True,
            "lead_id": dup_id,
            "duplicate": True,
            "country": country,
            "db_path": db_path,
        })

    sql = (
        "INSERT INTO leads (name, phone, address, city, province, product_qty, product_value, status, event_id, created_at, phone_e164, blocked, updated_at, notes, country) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )

    try:
        con = sqlite3.connect(db_path)
        con.execute("PRAGMA journal_mode=WAL;")
        ensure_leads_tracking_columns(con)
        ensure_master_history_tables(con)
        cur = con.cursor()
        cur.execute(sql, (name, phone_norm, address, city, province, qty, value, "novo", event_id, created_at, phone_e164, 0, updated_at, notes, country))
        lead_id = cur.lastrowid
        record_status_snapshot(cur, lead_id, "novo", "pedido_rapido_created", created_at)
        panel_event_id = "%s-ADMIN-%s" % ((country or "EC").strip().upper(), lead_id)
        cur.execute("UPDATE leads SET event_id=?, updated_at=? WHERE id=?", (panel_event_id, updated_at, int(lead_id)))
        con.commit()
        con.close()
    except Exception as e:
        return jsonify({"ok": False, "error": f"DB: {type(e).__name__}: {e}", "db_path": db_path}), 500

    payload = {
        "event": "pedido_novo",
        "origem": "maxlien",
        "pais": country,
        "nome": name,
        "telefone": phone_e164 or phone,
        "cidade": city,
        "provincia": province,
        "endereco": address,
        "produto": product,
        "quantidade": qty,
        "valor": value,
        "lead_id": lead_id,
        "event_id": event_id,
    }

    sent_ok, sent_detail = _pr_send_smclick(payload)
    return jsonify({
        "ok": True,
        "lead_id": lead_id,
        "event_id": event_id,
        "country": country,
        "db_path": db_path,
        "smclick_ok": sent_ok,
        "smclick_detail": sent_detail,
    })

# === AUTO: PEDIDO RAPIDO JSON APPEND (END) ===

# MX_META_EXCEPT_SRC_TAG_V1_20260224
