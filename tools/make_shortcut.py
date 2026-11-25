#!/usr/bin/env python3
"""Собирает неподписанный файл быстрой команды OTA Installer.shortcut (дальше его подписывает `shortcuts sign`)."""
import json
import plistlib
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
cfg = json.loads((ROOT / "config.json").read_text("utf-8"))
site = (cfg.get("site_url") or f'https://{cfg["github_user"]}.github.io/{cfg["repo"]}').rstrip("/")
title = cfg.get("store_name", "OTA Installer")
out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "unsigned.shortcut"

U = lambda: str(uuid.uuid4()).upper()
u_url, u_get, u_names, u_count, u_if, u_pick, u_inst, u_link = (U() for _ in range(8))
if_group = U()


def out_ref(uid, name):
    return {"Value": {"Type": "ActionOutput", "OutputUUID": uid, "OutputName": name},
            "WFSerializationType": "WFTextTokenAttachment"}


def text_with(uid, name):
    return {"Value": {"string": "￼", "attachmentsByRange": {
        "{0, 1}": {"Type": "ActionOutput", "OutputUUID": uid, "OutputName": name}}},
        "WFSerializationType": "WFTextTokenString"}


def act(ident, **params):
    return {"WFWorkflowActionIdentifier": f"is.workflow.actions.{ident}", "WFWorkflowActionParameters": params}


actions = [
    act("url", UUID=u_url, WFURLActionURL=f"{site}/apps.json"),
    act("downloadurl", UUID=u_get, WFInput=out_ref(u_url, "URL"), WFHTTPMethod="GET"),
    act("getvalueforkey", UUID=u_names, WFInput=out_ref(u_get, "Contents of URL"),
        WFGetDictionaryValueType="Value", WFDictionaryKey="names"),
    act("count", UUID=u_count, Input=out_ref(u_names, "Dictionary Value"), WFCountType="Items"),
    # если пусто — сообщение и выход
    act("conditional", GroupingIdentifier=if_group, WFControlFlowMode=0, UUID=u_if,
        WFInput={"Type": "Variable", "Variable": out_ref(u_count, "Count")},
        WFCondition=0, WFNumberValue=1),  # 0 = "меньше чем" 1
    act("alert", WFAlertActionTitle=title, WFAlertActionMessage="Пока нечего ставить — залей IPA с Мака.",
        WFAlertActionCancelButtonShown=False),
    act("exit"),
    act("conditional", GroupingIdentifier=if_group, WFControlFlowMode=2),
    act("choosefromlist", UUID=u_pick, WFInput=out_ref(u_names, "Dictionary Value"),
        WFChooseFromListActionPrompt="Что установить?"),
    act("getvalueforkey", UUID=u_inst, WFInput=out_ref(u_get, "Contents of URL"),
        WFGetDictionaryValueType="Value", WFDictionaryKey="install"),
    act("getvalueforkey", UUID=u_link, WFInput=out_ref(u_inst, "Dictionary Value"),
        WFGetDictionaryValueType="Value", WFDictionaryKey=text_with(u_pick, "Chosen Item")),
    act("openurl", WFInput=out_ref(u_link, "Dictionary Value")),
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
    "WFWorkflowActions": actions,
}
out.write_bytes(plistlib.dumps(shortcut, fmt=plistlib.FMT_BINARY))
print(out)
