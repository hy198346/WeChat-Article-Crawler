import json, sys, os, traceback

_cwd = os.getcwd()
_here = os.path.dirname(os.path.abspath(__file__))
_project = os.path.abspath(os.path.join(_here, os.pardir))
if _project not in sys.path:
    sys.path.insert(0, _project)

from scripts.wechat_article_crawler import bootstrap_refresh_auth as _impl

def _root():
    return _impl._repo_root()

def _fallback_summary(*, ok, updated, error=""):
    cfg_path = _root() / "config.json"
    token = ""
    cookie_present = False
    cookie_digest = ""
    try:
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8")) or {}
            token = str(cfg.get("token") or "")
            cookie = str(cfg.get("cookie") or "")
            cookie_present = bool(cookie)
            if cookie_present and len(cookie) > 10:
                cookie_digest = cookie[:4] + "…" + cookie[-4:]
            elif cookie_present:
                cookie_digest = cookie
    except Exception:
        pass
    payload = {
        "ok": bool(ok),
        "updated": bool(updated),
        "token": token,
        "cookie_present": cookie_present,
        "cookie_digest": cookie_digest,
        "config_path": str(cfg_path),
    }
    if error:
        payload["error"] = str(error)
    print(json.dumps(payload, ensure_ascii=False))

if __name__ == "__main__":
    try:
        _impl.main()
    except SystemExit as e:
        rc = int(e.code or 0)
        if rc != 0:
            _fallback_summary(
                ok=False,
                updated=False,
                error=f"bootstrap_refresh_auth exit={rc}",
            )
        raise
    except Exception as e:
        tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
        _fallback_summary(ok=False, updated=False, error=f"{type(e).__name__}: {e}; trace={tb}")
        sys.exit(1)

sys.modules[__name__] = _impl
