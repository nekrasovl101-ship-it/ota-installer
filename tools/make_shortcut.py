#!/usr/bin/env python3
"""Собирает неподписанный файл быстрой команды (потом его подписывает `shortcuts sign`).

Команда: выбрать IPA → выбрать сертификат (или «как есть») → GitHub подписывает → «Установить?».
Токен GitHub берётся из server/.token
"""
import json
import plistlib
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
cfg = json.loads((ROOT / "config.json").read_text("utf-8"))
site = (cfg.get("site_url") or f'https://{cfg["github_user"]}.github.io/{cfg["repo"]}').rstrip("/")
repo = f'{cfg["github_user"]}/{cfg["repo"]}'
title = cfg.get("store_name", "OTA Installer")
token = (ROOT / ".token").read_text().strip() if (ROOT / ".token").exists() else ""
if not token:
    sys.exit("нет server/.token")
out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "unsigned.shortcut"

U = lambda: str(uuid.uuid4()).upper()


def ref(uid, name):
    return {"Type": "ActionOutput", "OutputUUID": uid, "OutputName": name}


def att(uid, name):
    return {"Value": ref(uid, name), "WFSerializationType": "WFTextTokenAttachment"}


def var(uid, name):
    return {"Type": "Variable", "Variable": att(uid, name)}


def text(*parts):
    """parts: строки, (uuid, outputName) или ('@', 'Repeat Index')"""
    s, atts = "", {}
    for p in parts:
        if isinstance(p, str):
            s += p
        else:
            a = {"Type": "Variable", "VariableName": p[1]} if p[0] == "@" else ref(*p)
            atts[f"{{{len(s.encode('utf-16-le')) // 2}, 1}}"] = a
            s += "￼"
    return {"Value": {"string": s, "attachmentsByRange": atts}, "WFSerializationType": "WFTextTokenString"}


def act(ident, **params):
    return {"WFWorkflowActionIdentifier": f"is.workflow.actions.{ident}", "WFWorkflowActionParameters": params}


def get(uid, src, key):
    return act("getvalueforkey", UUID=uid, WFInput=att(*src), WFGetDictionaryValueType="Value", WFDictionaryKey=key)


def if_(group, input_var, cond, **kw):
    return act("conditional", GroupingIdentifier=group, WFControlFlowMode=0, WFInput=input_var, WFCondition=cond, **kw)


def end_if(group):
    return act("conditional", GroupingIdentifier=group, WFControlFlowMode=2)


def alert(*msg):
    return act("alert", WFAlertActionTitle=title, WFAlertActionMessage=text(*msg), WFAlertActionCancelButtonShown=False)


def header(k, v):
    return {"WFItemType": 0, "WFKey": text(k), "WFValue": text(v)}


(rnd, apps, names, cnt, pick, keys, key, certs, menu, cpick, asis, inst, link,
 ids, cid, body, post, perr, job, st, url, msg) = (U() for _ in range(22))
g_empty, g_asis, g_perr, g_done, g_err, g_loop = (U() for _ in range(6))

A = [
    act("number.random", UUID=rnd, WFRandomNumberMinimum=100000, WFRandomNumberMaximum=999999),
    # --- список приложений ---
    act("downloadurl", UUID=apps, WFHTTPMethod="GET", WFURL=text(f"{site}/apps.json?t=", (rnd, "Random Number"))),
    get(names, (apps, "Contents of URL"), "names"),
    act("count", UUID=cnt, Input=att(names, "Dictionary Value"), WFCountType="Items"),
    if_(g_empty, var(cnt, "Count"), 0, WFNumberValue=1),
    alert("Пока нечего ставить — залей IPA с Мака."),
    act("exit"),
    end_if(g_empty),
    act("choosefromlist", UUID=pick, WFInput=att(names, "Dictionary Value"), WFChooseFromListActionPrompt="Что установить?"),
    get(keys, (apps, "Contents of URL"), "keys"),
    get(key, (keys, "Dictionary Value"), text((pick, "Chosen Item"))),
    # --- сертификаты ---
    act("downloadurl", UUID=certs, WFHTTPMethod="GET", WFURL=text(f"{site}/certs.json?t=", (rnd, "Random Number"))),
    get(menu, (certs, "Contents of URL"), "menu"),
    act("choosefromlist", UUID=cpick, WFInput=att(menu, "Dictionary Value"), WFChooseFromListActionPrompt="Каким сертификатом?"),
    get(asis, (certs, "Contents of URL"), "asIs"),
    # «как есть» — ставим сразу
    if_(g_asis, var(cpick, "Chosen Item"), 4, WFConditionalActionString=text((asis, "Dictionary Value"))),
    get(inst, (apps, "Contents of URL"), "install"),
    get(link, (inst, "Dictionary Value"), text((pick, "Chosen Item"))),
    act("openurl", WFInput=att(link, "Dictionary Value")),
    act("exit"),
    end_if(g_asis),
    # --- запускаем подпись на GitHub ---
    get(ids, (certs, "Contents of URL"), "ids"),
    get(cid, (ids, "Dictionary Value"), text((cpick, "Chosen Item"))),
    act("gettext", UUID=body, WFTextActionText=text(
        '{"ref":"main","inputs":{"app":"', (key, "Dictionary Value"),
        '","cert":"', (cid, "Dictionary Value"),
        '","req":"', (rnd, "Random Number"), '"}}')),
    act("downloadurl", UUID=post, WFHTTPMethod="POST", WFHTTPBodyType="File",
        WFURL=text(f"https://api.github.com/repos/{repo}/actions/workflows/sign.yml/dispatches"),
        WFRequestVariable=att(body, "Text"),
        ShowHeaders=True,
        WFHTTPHeaders={"Value": {"WFDictionaryFieldValueItems": [
            header("Authorization", f"Bearer {token}"),
            header("Accept", "application/vnd.github+json"),
            header("Content-Type", "application/json"),
            header("User-Agent", "OTA-Installer"),
        ]}, "WFSerializationType": "WFDictionaryFieldValue"}),
    get(perr, (post, "Contents of URL"), "message"),
    if_(g_perr, var(perr, "Dictionary Value"), 100),
    alert("GitHub не принял запрос: ", (perr, "Dictionary Value"), "\nПроверь токен (пересобери команду)."),
    act("exit"),
    end_if(g_perr),
    act("notification", WFNotificationActionTitle=title,
        WFNotificationActionBody="Подписываю на GitHub… обычно 1–3 минуты"),
    # --- ждём результат ---
    act("repeat.count", GroupingIdentifier=g_loop, WFControlFlowMode=0, WFRepeatCount=45),
    act("delay", WFDelayTime=6),
    act("downloadurl", UUID=job, WFHTTPMethod="GET",
        WFURL=text(f"{site}/jobs/", (rnd, "Random Number"), ".json?n=", ("@", "Repeat Index"))),
    get(st, (job, "Contents of URL"), "status"),
    if_(g_done, var(st, "Dictionary Value"), 4, WFConditionalActionString="done"),
    get(url, (job, "Contents of URL"), "installURL"),
    act("openurl", WFInput=att(url, "Dictionary Value")),
    act("exit"),
    end_if(g_done),
    if_(g_err, var(st, "Dictionary Value"), 4, WFConditionalActionString="error"),
    get(msg, (job, "Contents of URL"), "message"),
    alert("Не подписалось: ", (msg, "Dictionary Value")),
    act("exit"),
    end_if(g_err),
    act("repeat.count", GroupingIdentifier=g_loop, WFControlFlowMode=2),
    alert("Что-то долго. Глянь github.com/" + repo + "/actions"),
]

shortcut = {
    "WFWorkflowClientVersion": "2605.0.5",
    "WFWorkflowMinimumClientVersion": 900,
    "WFWorkflowMinimumClientVersionString": "900",
    "WFWorkflowName": title,
    "WFWorkflowIcon": {"WFWorkflowIconStartColor": 2071128575, "WFWorkflowIconGlyphNumber": 61440},
    "WFWorkflowImportQuestions": [],
    "WFWorkflowTypes": [],
    "WFWorkflowInputContentItemClasses": [],
    "WFWorkflowOutputContentItemClasses": [],
    "WFWorkflowHasShortcutInputVariables": False,
    "WFWorkflowHasOutputFallback": False,
    "WFQuickActionSurfaces": [],
    "WFWorkflowActions": A,
}
out.write_bytes(plistlib.dumps(shortcut, fmt=plistlib.FMT_BINARY))
print(out)
