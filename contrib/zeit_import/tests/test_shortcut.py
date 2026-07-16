import os
import plistlib

import pytest

SHORTCUT_PATH = os.path.join(os.path.dirname(__file__), "..", "shortcut", "tandoor-zeit-import.shortcut")


@pytest.fixture(scope="module")
def workflow():
    with open(SHORTCUT_PATH, "rb") as f:
        return plistlib.load(f)


@pytest.fixture(scope="module")
def actions(workflow):
    return workflow["WFWorkflowActions"]


def test_action_order(actions):
    assert [a["WFWorkflowActionIdentifier"] for a in actions] == [
        "is.workflow.actions.vpn.set",       # VPN on
        "is.workflow.actions.delay",         # wait 1s for the tunnel
        "is.workflow.actions.downloadurl",   # POST to the import service
        "is.workflow.actions.showresult",    # show the report
        "is.workflow.actions.vpn.set",       # VPN off
    ]


def test_vpn_connect_and_disconnect(actions):
    connect, disconnect = actions[0], actions[4]
    # default operation is Connect -> WFVPNOperation omitted
    assert "WFVPNOperation" not in connect["WFWorkflowActionParameters"]
    assert disconnect["WFWorkflowActionParameters"]["WFVPNOperation"] == "Disconnect"
    # configuration reference left unset so the user picks their WireGuard tunnel
    assert "WFVPN" not in connect["WFWorkflowActionParameters"]
    assert "WFVPN" not in disconnect["WFWorkflowActionParameters"]


def test_delay_one_second(actions):
    assert actions[1]["WFWorkflowActionParameters"]["WFDelayTime"] == 1


def test_download_url_action(actions):
    params = actions[2]["WFWorkflowActionParameters"]
    assert params["WFURL"] == "https://CHANGE-ME.example/zeit/import"
    assert params["WFHTTPMethod"] == "POST"
    assert params["WFHTTPBodyType"] == "Form"
    headers = params["WFHTTPHeaders"]["Value"]["WFDictionaryFieldValueItems"]
    assert headers[0]["WFKey"]["Value"]["string"] == "X-Api-Key"
    assert headers[0]["WFValue"]["Value"]["string"] == "CHANGE-ME"
    form = params["WFFormValues"]["Value"]["WFDictionaryFieldValueItems"]
    assert form[0]["WFKey"]["Value"]["string"] == "url"
    attachments = form[0]["WFValue"]["Value"]["attachmentsByRange"]
    assert [a["Type"] for a in attachments.values()] == ["ExtensionInput"]


def test_show_result_renders_download_output(actions):
    download_uuid = actions[2]["WFWorkflowActionParameters"]["UUID"]
    text = actions[3]["WFWorkflowActionParameters"]["Text"]
    attachments = list(text["Value"]["attachmentsByRange"].values())
    assert attachments[0]["Type"] == "ActionOutput"
    assert attachments[0]["OutputUUID"] == download_uuid


def test_share_sheet_url_input(workflow):
    assert "WFURLContentItem" in workflow["WFWorkflowInputContentItemClasses"]
    assert "ActionExtension" in workflow["WFWorkflowTypes"]
