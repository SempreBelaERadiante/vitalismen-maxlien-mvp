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

PIXEL_EC_DEFAULT = "1338093710980688"
PIXEL_CO_DEFAULT = "3711029599192190"

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
    return px_co if h.startswith("co.") else px_ec

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
        token = (os.getenv("MVP_FB_ACCESS_TOKEN_EC") or "").strip()
        pixel_id = (os.getenv("MVP_FB_PIXEL_ID_EC") or PIXEL_EC_DEFAULT).strip()

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
        token = (os.getenv("MVP_FB_ACCESS_TOKEN_EC") or "").strip()
        pixel_id = (os.getenv("MVP_FB_PIXEL_ID_EC") or PIXEL_EC_DEFAULT).strip()

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
from flask import Flask, jsonify, redirect, render_template, render_template_string, request, session, url_for, flash, has_request_context

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

def send_meta_capi_event(event_name: str, event_id: str, phone_norm: str, value: float | None = None, currency: str | None = None) -> dict:

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

    if host == "co.maxlien.shop" or host.endswith(".co.maxlien.shop"):
        # CO — FIXO
        pixel_id = (os.getenv("MVP_FB_PIXEL_ID_CO") or PIXEL_CO_DEFAULT).strip()
        FB_ACCESS_TOKEN = (os.getenv("MVP_FB_ACCESS_TOKEN_CO") or "").strip()
    else:
        # EC — FIXO (não mexer)
        pixel_id = (os.getenv("MVP_FB_PIXEL_ID_EC") or PIXEL_EC_DEFAULT).strip()
        FB_ACCESS_TOKEN = (os.getenv("MVP_FB_ACCESS_TOKEN_EC") or "").strip()

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
    conn.commit()
    conn.close()


ensure_schema()


# =========================
# UTIL
# =========================
def safe_str(x):
    if x is None:
        return ""
    return str(x).strip()


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
    if session.get("admin_ok") is True:
        return None
    return redirect("/admin/login", code=302)

def status_class(status_value):
    s = str(status_value or "novo").strip().lower()
    return {
        "novo": "st-gray",
        "atendendo": "st-blue",
        "confirmado": "st-green",
        "pago": "st-yellow",
        "entregue": "st-green",
        "cancelado": "st-red",
        "devolvido": "st-red",
    }.get(s, "st-gray")

DASHBOARD_HTML = """<!doctype html>
<html><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Painel Unificado</title>
<style>
body{font-family:Arial;background:#0b0b0b;color:#fff;margin:0}
.wrap{max-width:1280px;margin:0 auto;padding:18px}
.badge{display:inline-block;padding:6px 12px;border-radius:999px;font-weight:700;margin-bottom:12px}
.badge-ec{background:#14532d;color:#d1fae5}.badge-co{background:#1d4ed8;color:#dbeafe}
table{width:100%;border-collapse:collapse;background:#111;border-radius:12px;overflow:hidden}
th,td{border-bottom:1px solid #222;padding:10px;text-align:left;font-size:14px;vertical-align:top}
th{background:#151515}.muted{color:#aaa}.st{padding:6px 10px;border-radius:999px;font-weight:700;font-size:12px;display:inline-block}
.st-gray{background:#374151}.st-blue{background:#1d4ed8}.st-green{background:#15803d}.st-yellow{background:#ca8a04;color:#111}.st-red{background:#b91c1c}
a{color:#93c5fd}
</style></head><body>
<div class="wrap">
{% set host = request.host or '' %}
{% set sigla = 'CO' if 'colombia.maxtourus.com.br' in host else 'EC' %}
<div class="badge {{ 'badge-co' if sigla == 'CO' else 'badge-ec' }}">Painel {{ sigla }}</div>
<h2>Leads Recentes</h2>
<p class="muted">Painel unificado por domínio. Cada site grava no seu banco pela sigla.</p>
<table>
<thead><tr><th>ID</th><th>Data</th><th>Nome</th><th>Telefone</th><th>Cidade</th><th>Província</th><th>Qtd</th><th>Valor</th><th>Status</th><th>País</th></tr></thead>
<tbody>
{% for r in rows %}
<tr>
<td>{{ r[0] if r|length > 0 else '' }}</td>
<td>{{ fmt_created_at_display(r[10] if r|length > 10 else '') }}</td>
<td>{{ r[1] if r|length > 1 else '' }}</td>
<td>{{ r[2] if r|length > 2 else '' }}</td>
<td>{{ r[3] if r|length > 3 else '' }}</td>
<td>{{ r[4] if r|length > 4 else '' }}</td>
<td>{{ r[6] if r|length > 6 else '' }}</td>
<td>{{ r[7] if r|length > 7 else '' }}</td>
<td><span class="st {{ status_class(r[8] if r|length > 8 else 'novo') }}">{{ r[8] if r|length > 8 else 'novo' }}</span></td>
<td>{{ r[9] if r|length > 9 else sigla }}</td>
</tr>
{% endfor %}
</tbody>
</table>
</div></body></html>"""


# =========================
# HEALTH
# =========================
# =========================
# PRICING (CO)
# =========================
CO_PRICE_MAP = {
    1: 170000,
    2: 300000,
    3: 350000,
    6: 584000,
}

def _co_price_for_qty(qty):
    pv = _price_for('CO', qty)
    return pv


@app.route("/health")
def health():
    return jsonify({"ok": True, "ts": int(time.time())})


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
    # =========================
    # DEDUP (6h)
    # =========================
    dup_id = find_recent_duplicate_lead_id(phone_norm, window_seconds=6 * 3600)
    if dup_id:
        wa_resp = {"wa_redirect": False, "wa_block_reason": "duplicate"}
        return jsonify({"ok": True, "lead_id": dup_id, "duplicate": True, **wa_resp})

    conn = db_conn()
    cur = conn.cursor()
    # PATCH: MX_LEAD_EVENTID_DBWRITE_V4_3B_20260221_002020
    # event_id p/ dedupe (Pixel <-> CAPI). Se client nao mandar, gera tmp ate termos lead_id.
    client_eid = (safe_str(data.get('event_id')) or '').strip()
    tmp_eid = __import__('uuid').uuid4().hex
    lead_event_id = client_eid if client_eid else f"lead_tmp_{tmp_eid}"

    cur.execute(
        "INSERT INTO leads (name, phone, city, province, address, product_qty, product_value, status, country, created_at, event_id, phone_e164) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            now_iso_utc(),
            # PATCH: MX_LEAD_EVENTID_DBWRITE_V4_3F_20260221_002818
            lead_event_id,
            phone_norm,
        ),
    )
    lead_id = cur.lastrowid
    # PATCH: MX_LEAD_EVENTID_DBWRITE_V4_3B_POST
    # Se o client nao mandou event_id, troca para lead_submit_{lead_id} e persiste
    if not client_eid:
        # PATCH: MX_LEAD_EVENTID_DBWRITE_V4_3F_DISABLE_FORCE_SUBMIT
        # lead_event_id = "lead_submit_%s" % (lead_id,)
        try:
            cur.execute("UPDATE leads SET event_id=? WHERE id=?", (lead_event_id, int(lead_id)))
        except Exception:
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
    wa_num = (_mvp_wa_number_for_host(host_for_wa) or WA_ME_NUMBER or "").strip()

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
                wa_resp = {"wa_redirect": True, "wa_url": wa_url, "wa_block_reason": ""}
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
.st-atendendo{background:#163b5c;border:1px solid #2c6fa3;color:#e6f2ff;}
.st-confirmado{background:#1e4d2b;border:1px solid #2f7a44;color:#ffffff;}
.st-pago{background:#0f5c3b;border:1px solid #1f8a5f;color:#ffffff;}
.st-entregue{background:#124c3b;border:1px solid #1f7a60;color:#ffffff;}
.st-devolvido{background:#5a3b13;border:1px solid #8b5a1e;color:#fff3e0;}
.st-cancelado{background:#5a1a1a;border:1px solid #8b2a2a;color:#ffecec;}

/* badge padrão */
.st-novo,.st-atendendo,.st-confirmado,.st-pago,.st-entregue,.st-devolvido,.st-cancelado{
  padding:6px 12px;border-radius:999px;font-weight:800;text-transform:lowercase;display:inline-block
}

/* select do status (dropdown) */
.status-pick{
  padding:6px 10px;border-radius:999px;font-weight:800;border:1px solid #444;
  background:#111;color:#eee;
}
.status-pick.st-novo,.status-pick.st-atendendo,.status-pick.st-confirmado,.status-pick.st-pago,
.status-pick.st-entregue,.status-pick.st-devolvido,.status-pick.st-cancelado{
  /* herda as cores do status */
  border-color: inherit;
}

/* === STATUS COLORS UNIFIED === */
.st-novo{background:#2b2b2b;border:2px solid #444;color:#e0e0e0;}
.st-atendendo{background:#163b5c;border:2px solid #2c6fa3;color:#e6f2ff;}
.st-confirmado{background:#1e4d2b;border:2px solid #2f7a44;color:#ffffff;}
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
select.status-pick.st-atendendo{background:#163b5c;border-color:#2c6fa3;color:#e6f2ff;}
select.status-pick.st-confirmado{background:#1e4d2b;border-color:#2f7a44;color:#fff;}
select.status-pick.st-pago{background:#0f5c3b;border-color:#1f8a5f;color:#fff;}
select.status-pick.st-entregue{background:#124c3b;border-color:#1f7a60;color:#fff;}
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
select.status-pick.st-atendendo { background:#1e5aa8; color:#fff; }
select.status-pick.st-confirmado{ background:#1f8a4c; color:#fff; }
select.status-pick.st-entregue  { background:#0f6b3b; color:#fff; }
select.status-pick.st-devolvido { background:#b26a00; color:#fff; }
select.status-pick.st-cancelado { background:#b32020; color:#fff; }
select.status-pick.st-pago      { background:#6a2fb8; color:#fff; }

.badge-saved{
  display:inline-block;
  margin-left:8px;
  font-size:12px;
  opacity:.85;
}

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
// === quick status colorize ===
(function(){
  function apply(sel){
    try{
      var v = (sel.value || "novo").toLowerCase().trim();
      sel.classList.remove("st-novo","st-atendendo","st-confirmado","st-entregue","st-devolvido","st-cancelado","st-pago");
      sel.classList.add("st-"+v);
    }catch(e){}
  }
  document.addEventListener("DOMContentLoaded", function(){
    document.querySelectorAll("select.status-pick").forEach(function(sel){
      apply(sel);
      sel.addEventListener("change", function(){ apply(sel); });
    });
  });
})();

// === STATUS AUTOSAVE FINAL ===
(function(){
  function paint(sel){
    var v = (sel.value || "novo").toLowerCase();
    sel.className = "status-pick st-" + v;
  }

  function save(sel){
    var form = sel.closest("form");
    if(!form) return;
    var url  = form.action;
    var data = new FormData(form);

    fetch(url, { method:"POST", body:data, credentials:"same-origin" })
      .then(function(r){ if(!r.ok) throw new Error("bad"); })
      .catch(function(){ alert("Erro ao salvar status"); });
  }

  document.addEventListener("DOMContentLoaded", function(){
    document.querySelectorAll("select.status-pick").forEach(function(sel){
      paint(sel);
      sel.addEventListener("change", function(){
        paint(sel);
        save(sel);
      });
    });
  });
})();
</script>

<script src="/static/status.js"></script>

<script>
(function(){
  const mapClass = (v) => {
    v = (v || '').toString().trim().toLowerCase();
    const m = {
      "novo":"st-novo",
      "atendendo":"st-atendendo",
      "confirmado":"st-confirmado",
      "entregue":"st-entregue",
      "devolvido":"st-devolvido",
      "cancelado":"st-cancelado",
      "pago":"st-pago",
    };
    return m[v] || "st-novo";
  };

  const applyColor = (sel) => {
    sel.classList.remove("st-novo","st-atendendo","st-confirmado","st-entregue","st-devolvido","st-cancelado","st-pago");
    sel.classList.add(mapClass(sel.value));
  };

  const showSaved = (sel, ok, msg) => {
    let b = sel.parentElement.querySelector(".badge-saved");
    if(!b){
      b = document.createElement("span");
      b.className = "badge-saved";
      sel.parentElement.appendChild(b);
    }
    b.textContent = ok ? "salvo" : ("erro" + (msg ? (": "+msg) : ""));
    b.style.opacity = "0.9";
    clearTimeout(b._t);
    b._t = setTimeout(()=>{ b.style.opacity = "0"; }, ok ? 900 : 2000);
  };

  async function saveStatus(id, status){
    const r = await fetch("/admin/api/status", {
      method:"POST",
      headers:{ "Content-Type":"application/json" },
      body: JSON.stringify({ id, status })
    });
    const j = await r.json().catch(()=>({ok:false, error:"json"}));
    if(!r.ok || !j.ok) throw new Error(j.error || ("http_"+r.status));
    return j;
  }

  window.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("select.status-pick").forEach(sel => {
      applyColor(sel);

      sel.addEventListener("change", async () => {
        const id = sel.getAttribute("data-lead-id");
        const prev = sel.getAttribute("data-prev") || sel.value;
        const now = sel.value;

        // cor na hora
        applyColor(sel);

        // salva
        sel.disabled = true;
        try{
          await saveStatus(id, now);
          sel.setAttribute("data-prev", now);
          showSaved(sel, true);
        }catch(e){
          // volta pro anterior se falhar
          sel.value = prev;
          applyColor(sel);
          showSaved(sel, false, e.message);
        }finally{
          sel.disabled = false;
        }
      });

      sel.setAttribute("data-prev", sel.value);
    });
  });
})();
</script>

</body></html>
"""



@app.route("/admin", methods=["GET"])
def admin_selector():
    return redirect("/admin/dashboard", code=302)

@app.route("/admin/dashboard")
def admin_dashboard():
    guard = admin_required()
    if guard: return guard
    conn = db_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM leads ORDER BY id DESC LIMIT 500")
    rows = cur.fetchall()
    conn.close()
    return render_template_string(DASHBOARD_HTML, rows=rows, fmt_created_at_display=fmt_created_at_display, status_class=status_class)


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
<form method="post">
<label>Nome</label><input name="name" value="{{lead.name}}">
<div class="row">
<div><label>Telefone</label><input name="phone" value="{{lead.phone}}"></div>
<div><label>Status</label>
<select name="status" class="status-pick" data-lead-id="{{ lead.id }}">
{% for s in statuses %}<option value="{{s}}" {% if lead.status==s %}selected{% endif %}>{{s}}</option>{% endfor %}
</select></div>
</div>
<div class="row">
<div><label>Cidade</label><input name="city" value="{{lead.city}}"></div>
<div><label>Província</label><input name="province" value="{{lead.province}}"></div>
</div>
<label>Endereço</label><textarea name="address">{{lead.address}}</textarea>
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

    conn = db_conn()
    cur = conn.cursor()

    if request.method == "POST":

        # ===== NOVO: criar lead quando lead_id == 0 (reusa o mesmo formulário) =====
        if lead_id == 0:
            name = safe_str(request.form.get("name"))
            phone_norm = normalize_phone(request.form.get("phone"))
            city = safe_str(request.form.get("city"))
            province = safe_str(request.form.get("province"))
            address = safe_str(request.form.get("address"))
            product_qty = safe_str(request.form.get("product_qty"))
            product_value = safe_str(request.form.get("product_value"))
            status = safe_str(request.form.get("status") or "novo") or "novo"
            country = safe_str(request.form.get("country"))
            # AUTO-PREÇO CO (COP) por quantidade
            if (country or "").strip().upper() == "CO":
                pv = _co_price_for_qty(product_qty)
                if pv is not None:
                    product_value = str(pv)
            source = "manual"

            if not name or not phone_norm:
                flash("Nome e telefone são obrigatórios para incluir cliente.", "error")
                return redirect(url_for("admin_new_lead"))

            # dup por telefone normalizado
            conn2 = db_conn()
            cur2 = conn2.cursor()
            try:
                cur2.execute("SELECT id FROM leads WHERE phone = ? ORDER BY id DESC LIMIT 1", (phone_norm,))
                dup = cur2.fetchone()
            except Exception:
                dup = None

            if dup and dup[0]:
                conn2.close()
                flash("Este telefone já está cadastrado. Abra o cadastro existente para editar.", "warning")
                return redirect(url_for("admin_edit_lead", lead_id=int(dup[0])))

            new_id = None
            try:
                import time
                ts = int(time.time())
                cur2.execute(
                    "INSERT INTO leads (name, phone, city, province, address, product_qty, product_value, status, country, created_at, source) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (name, phone_norm, city, province, address, product_qty, product_value, status, country, ts, source),
                )
                new_id = cur2.lastrowid
                conn2.commit()
                conn2.close()
            except Exception:
                conn2.rollback()
                try:
                    cur2.execute(
                        "INSERT INTO leads (name, phone, city, province, address, product_qty, product_value, status, country, source) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (name, phone_norm, city, province, address, product_qty, product_value, status, country, source),
                    )
                    new_id = cur2.lastrowid
                    conn2.commit()
                    conn2.close()
                except Exception as e2:
                    conn2.rollback()
                    conn2.close()
                    flash(f"Falha ao incluir cliente: {e2}", "error")
                    return redirect(url_for("admin_new_lead"))

            flash("Cliente incluído com sucesso.", "success")
            return redirect(url_for("admin_edit_lead", lead_id=int(new_id)))
        # ===== FIM NOVO =====
        # Captura status anterior (para detectar transição -> confirmado)
        prev_status = ""
        try:
            cur2 = conn.cursor()
            cur2.execute("SELECT status FROM leads WHERE id=?", (lead_id,))
            r0 = cur2.fetchone()
            if r0:
                prev_status = (r0[0] or "")
        except Exception:
            prev_status = ""

        # Novos valores (normalizados)
        name = safe_str(request.form.get("name"))
        phone_norm = normalize_phone(request.form.get("phone"))
        city = safe_str(request.form.get("city"))
        province = safe_str(request.form.get("province"))
        address = safe_str(request.form.get("address"))
        product_qty = safe_str(request.form.get("product_qty"))
        product_value = safe_str(request.form.get("product_value"))
        new_status = safe_str(request.form.get("status"))
        country = safe_str(request.form.get("country"))

        cur.execute(
            "UPDATE leads SET name=?, phone=?, city=?, province=?, address=?, product_qty=?, product_value=?, status=?, country=? WHERE id=?",
            (name, phone_norm, city, province, address, product_qty, product_value, new_status, country, lead_id),
        )
        conn.commit()

        # Gatilho META (TREINO): Purchase quando status vira "confirmado"
        try:
            if (prev_status or "").lower() != "confirmado" and (new_status or "").lower() == "confirmado":
                pv_num = None
                try:
                    pv_num = float((product_value or "").replace(",", "."))
                except Exception:
                    pv_num = None

                event_id = f"purchase_confirmado_lead_{lead_id}"
                capi = send_meta_capi_event("Purchase", event_id=event_id, phone_norm=phone_norm, value=pv_num, currency=None)
                try:
                    app.logger.info(f"Meta Purchase(CONFIRMADO) lead={lead_id} prev={prev_status} new={new_status} capi_ok={capi.get('ok')}")
                except Exception:
                    pass
        except Exception as e:
            try:
                app.logger.exception(f"Erro gatilho Meta Purchase(CONFIRMADO) lead {lead_id}: {e}")
            except Exception:
                pass

        conn.close()
        return redirect("/admin/dashboard")

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
            return render_template_string(EDIT_HTML, lead=lead, statuses=STATUSES, r=lead)
        abort(404)
    return render_template_string(EDIT_HTML, lead=lead, statuses=STATUSES, r=lead)
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

    conn = db_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    conn.commit()
    conn.close()
    return redirect("/admin/dashboard")



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
  <button type="submit">Entrar</button>
</form></div></body></html>
"""
# === /ADMIN LOGIN HTML (PROD - FIX) ===


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


### ADMIN_STATUS_QUICK_BEGIN ###
@app.route("/admin/status/<int:lead_id>", methods=["POST"])
def admin_quick_status(lead_id):
    guard = admin_required()
    if guard:
        return guard

    new_status = (request.form.get("status") or "").strip()
    allowed = {"novo","atendendo","confirmado","cancelado","devolvido","entregue","pago","finalizado"}
    if new_status and new_status not in allowed:
        return "Status inválido", 400

    conn = db_conn()
    cur = conn.cursor()
    # Atualiza somente o status
    cur.execute("UPDATE leads SET status=? WHERE id=?", (new_status, lead_id))
    conn.commit()
    conn.close()

    # volta para o dashboard (mantém simples e rápido)
    return redirect("/admin/dashboard")

### ADMIN_STATUS_QUICK_END ###


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
    cur.execute('SELECT status, phone, product_value FROM leads WHERE id=?', (int(lead_id),))
    row = cur.fetchone()
    if not row:
        conn.close()
        return ('not_found', 404)

    old_status = row[0]
    new_status = 'confirmado'

    cur.execute('UPDATE leads SET status=? WHERE id=?', (new_status, int(lead_id)))
    conn.commit()
    conn.close()

    try:
        if '_should_fire_purchase' in globals() and '_fb_capi_purchase_minimal' in globals():
            if _should_fire_purchase(old_status, new_status):
                lead_row = {'id': lead_id, 'status': new_status, 'phone': row[1], 'product_value': row[2]}
                ok, why = _fb_capi_purchase_minimal(lead_row, event_source_url=request.url)
                app.logger.info('FB CAPI Purchase PROD quick_confirm lead=%s ok=%s why=%s prev=%s new=%s', lead_id, ok, why, old_status, new_status)
    except Exception as e:
        try:
            app.logger.warning('FB CAPI quick_confirm error: %s', e)
        except Exception:
            pass

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
    host = request.host or ""
    country = _pr_country_from_host(host)
    db_path = _pr_db_path_from_host(host)

    if request.method == "GET":
        return jsonify({
            "ok": True,
            "route": "/admin/pedido-rapido",
            "host": host,
            "country": country,
            "db_path": db_path,
            "howto": "POST JSON ou form-data: name, phone, city, product_qty, product_value. province/address/notes/product opcionais."
        })

    data = request.get_json(silent=True) or {}
    if not data and request.form:
        data = dict(request.form)

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
    if qty < 1:
        qty = 1

    try:
        value = float(data.get("product_value") or 0.0)
    except Exception:
        value = 0.0

    event_id = uuid.uuid4().hex
    created_at = _pr_now_iso()
    updated_at = created_at
    phone_e164 = _pr_to_e164(phone, country)

    sql = (
        "INSERT INTO leads (name, phone, address, city, province, product_qty, product_value, status, event_id, created_at, phone_e164, blocked, updated_at, notes, country) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )

    try:
        con = sqlite3.connect(db_path)
        con.execute("PRAGMA journal_mode=WAL;")
        cur = con.cursor()
        cur.execute(sql, (name, phone, address, city, province, qty, value, "novo", event_id, created_at, phone_e164, 0, updated_at, notes, country))
        con.commit()
        lead_id = cur.lastrowid
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
