from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("TELEGRAM_API_ID", "1")
os.environ.setdefault("TELEGRAM_API_HASH", "x")
os.environ.setdefault(
    "TELEGRAM_SESSION",
    str(Path(tempfile.gettempdir()) / "praxis_registry_integration_test"),
)

from telethon.tl import functions, types  # noqa: E402

import mtproto_runner as runner  # noqa: E402


class FakeLiveClient:
    """Network-free stand-in for the one client owned by mtproto_runner."""

    def __init__(self):
        self.calls = []
        self.resolutions = []

    async def get_input_entity(self, value):
        self.resolutions.append(value)
        if value == "me":
            return types.InputPeerSelf()
        if value == "@room":
            return types.InputPeerChannel(channel_id=88, access_hash=808)
        return types.InputPeerUser(user_id=77, access_hash=707)

    async def __call__(self, request):
        self.calls.append(request)
        return {"message_id": 901, "peer_id": 77, "kind": type(request).__name__}


class LiveRegistryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.saved = {
            "client": runner.client,
            "owner_id": runner.OWNER_ID,
            "dispatcher": runner._TELEGRAM_DISPATCHER,
            "threadsafe": runner._threadsafe_result,
            "scope": runner.agent._CURRENT_SCOPE,
            "hook": runner.agent._TELETHON.get("telegram_account"),
            "owner_env": os.environ.get("PRAXIS_OWNER_ID"),
        }
        os.environ["PRAXIS_OWNER_ID"] = "42"
        self.channel_token = runner.agent._TURN_CHANNEL.set(runner.agent.ChannelContext(
            chat_id="42", principal_id=42, is_dm=True, owner=True, known=True,
        ))
        self.fake = FakeLiveClient()
        runner.client = self.fake
        runner.OWNER_ID = 42
        runner.agent._CURRENT_SCOPE = "owner"
        runner._threadsafe_result = lambda factory, timeout: asyncio.run(factory())
        dispatcher = runner._install_telegram_dispatcher()
        runner.agent._TELETHON["telegram_account"] = runner._sync_telegram_account
        self.origin = {
            "run_id": "run-owner-one", "chat_id": "42", "message_id": 100,
            "principal_id": "42", "is_dm": True, "raw_text": "do it",
        }
        self.execution = {
            "run_id": "run-owner-one", "call_id": "tool-one",
            "tool": "telegram_account", "args": {}, "side_effect": True,
            "idempotency_key": "",
        }
        self.origin_patch = mock.patch.object(
            runner.agent, "current_origin_evidence",
            side_effect=lambda: dict(self.origin),
        )
        self.execution_patch = mock.patch.object(
            runner.agent, "current_tool_execution",
            side_effect=lambda: dict(self.execution),
        )
        self.origin_patch.start()
        self.execution_patch.start()
        self.assertIs(dispatcher.caller, self.fake)

    def tearDown(self):
        runner.client = self.saved["client"]
        runner.OWNER_ID = self.saved["owner_id"]
        runner._TELEGRAM_DISPATCHER = self.saved["dispatcher"]
        runner._threadsafe_result = self.saved["threadsafe"]
        runner.agent._CURRENT_SCOPE = self.saved["scope"]
        if self.saved["hook"] is None:
            runner.agent._TELETHON.pop("telegram_account", None)
        else:
            runner.agent._TELETHON["telegram_account"] = self.saved["hook"]
        runner.agent._TURN_CHANNEL.reset(self.channel_token)
        if self.saved["owner_env"] is None:
            os.environ.pop("PRAXIS_OWNER_ID", None)
        else:
            os.environ["PRAXIS_OWNER_ID"] = self.saved["owner_env"]
        self.execution_patch.stop()
        self.origin_patch.stop()

    def tool(self, action, **kwargs):
        return runner.agent.tool_telegram_account(action, **kwargs)

    def test_tool_lists_searches_describes_and_calls_installed_schema(self):
        listed = json.loads(self.tool("list", limit=1))
        self.assertEqual(listed["action"], "list")
        self.assertEqual(len(listed["items"]), 1)

        searched = json.loads(self.tool("search", query="send message", limit=1))
        exact = searched["items"][0]["name"]
        self.assertEqual(exact, "functions.messages.SendMessageRequest")

        described = json.loads(self.tool("describe", request=exact))
        self.assertEqual(described["request"]["name"], exact)
        self.assertIn("peer", described["request"]["schema"]["required"])

        called = json.loads(
            self.tool(
                "call",
                request=exact,
                params_json=json.dumps({"peer": "me", "message": "hello"}),
            )
        )
        receipt = called["receipt"]
        self.assertEqual(receipt["status"], "ok", receipt)
        self.assertEqual(receipt["principal"], "42")
        self.assertEqual(receipt["policy"]["raw_sovereign_only"], True)
        self.assertTrue(receipt["policy"]["sovereign"])
        self.assertEqual(receipt["result"]["message_id"], 901)
        self.assertEqual(self.fake.resolutions, ["me"])
        self.assertEqual(len(self.fake.calls), 1)
        self.assertIsInstance(self.fake.calls[0], functions.messages.SendMessageRequest)
        self.assertIsInstance(self.fake.calls[0].peer, types.InputPeerSelf)

        channel_call = json.loads(
            self.tool(
                "call",
                request="functions.channels.GetFullChannelRequest",
                params_json='{"channel":"@room"}',
            )
        )
        self.assertEqual(channel_call["receipt"]["status"], "ok")
        self.assertIsInstance(self.fake.calls[1], functions.channels.GetFullChannelRequest)
        self.assertIsInstance(self.fake.calls[1].channel, types.InputChannel)

    def test_raw_alias_is_denied_without_echoing_login_secrets(self):
        alias = json.loads(
            self.tool(
                "call",
                request="messages.SendMessageRequest",
                params_json='{"peer":"me","message":"no"}',
            )
        )
        self.assertEqual(alias["receipt"]["status"], "denied")
        self.assertIn("exact constructor name", alias["receipt"]["error"]["message"])

        # Account-critical aliases resolve to the canonical descriptor before
        # dispatch, so even the denied receipt carries no submitted login parameters.
        login_secret = "otp-731946"
        critical_alias_raw = self.tool(
            "call", request="auth.SignInRequest",
            params_json=json.dumps({
                "phone_number": "+100000000",
                "phone_code_hash": "hash-one",
                "phone_code": login_secret,
            }),
        )
        critical_alias = json.loads(critical_alias_raw)
        self.assertEqual(critical_alias["receipt"]["status"], "denied")
        self.assertEqual(critical_alias["receipt"]["request_name"],
                         "functions.auth.SignInRequest")
        self.assertNotIn(login_secret, critical_alias_raw)
        self.assertEqual(self.fake.calls, [])

    def test_account_critical_call_runs_directly_and_echoes_nothing(self):
        # Егор 10.11 («Снять целиком»): ни челленджа, ни exact-фразы из новой лички —
        # account-critical вызов исполняется сразу. Остаётся только редактирование:
        # параметры и результат в квитанцию не попадают.
        raw = self.tool("call", request="functions.auth.LogOutRequest")
        called = json.loads(raw)
        receipt = called["receipt"]
        self.assertEqual(receipt["status"], "ok", receipt)
        self.assertEqual(receipt["policy"]["risk"], "account_critical")
        self.assertIsNone(receipt.get("submitted_parameters"))
        self.assertIsNone(receipt.get("result"))
        self.assertEqual(len(self.fake.calls), 1)
        self.assertIsInstance(self.fake.calls[0], functions.auth.LogOutRequest)
        self.assertNotIn("message_id", raw, "результат критичного вызова не эхоится")

    def test_old_confirmation_actions_are_gone(self):
        for action in ("confirm", "pending_confirmations", "cancel_confirmation"):
            answer = self.tool(action)
            self.assertIn("list | search | describe | call", answer, action)
        self.assertEqual(self.fake.calls, [])

    def test_praxis_self_is_sovereign(self):
        internal = runner.agent.ChannelContext(
            principal_id=runner.agent.PRAXIS_SELF_PRINCIPAL,
            is_dm=True, owner=False, known=True, _scope_override="owner",
        )
        token = runner.agent._TURN_CHANNEL.set(internal)
        try:
            result = json.loads(self.tool(
                "call", request="functions.messages.SendMessageRequest",
                params={"peer": "me", "message": "from Praxis"},
            ))
        finally:
            runner.agent._TURN_CHANNEL.reset(token)
        self.assertEqual(result["receipt"]["status"], "ok")
        self.assertEqual(result["receipt"]["principal"], runner.agent.PRAXIS_SELF_PRINCIPAL)
        self.assertEqual(len(self.fake.calls), 1)

        trusted = runner.agent.ChannelContext(
            chat_id="99", principal_id="99", is_dm=True, owner=False, known=True,
        )
        token = runner.agent._TURN_CHANNEL.set(trusted)
        try:
            # Решение Егора 26.07 (вариант 1): в её ходе действует ОНА, кто бы ни
            # заговорил. Сырой MTProto — её собственный аккаунт, и чужая реплика не
            # делает её на нём гостем. Раньше здесь ожидался отказ.
            allowed_by_agent = self.tool("search", query="send message")
            self.assertNotIn("Отказ", allowed_by_agent)
            allowed_by_runner = runner._sync_telegram_account(
                action="search", query="send message"
            )
            self.assertNotIn("Отказ", allowed_by_runner)
        finally:
            runner.agent._TURN_CHANNEL.reset(token)
        self.assertEqual(len(self.fake.calls), 1,
                         'search не доходит до фейкового клиента — важно, что он больше не отказ')

        # Even an erroneously owner-labelled context cannot substitute another
        # principal id for PRAXIS_OWNER_ID at the runner/dispatcher boundary.
        context = runner.agent.ChannelContext(
            chat_id="99", principal_id="99", is_dm=True, owner=True, known=True
        )
        token = runner.agent._TURN_CHANNEL.set(context)
        try:
            denied_wrong_principal = self.tool("search", query="send message")
        finally:
            runner.agent._TURN_CHANNEL.reset(token)
        self.assertIn("только владелец", denied_wrong_principal)
        self.assertEqual(len(self.fake.calls), 1)

    def test_praxis_self_runs_a_critical_action_herself(self):
        internal = runner.agent.ChannelContext(
            principal_id=runner.agent.PRAXIS_SELF_PRINCIPAL,
            is_dm=True, owner=False, known=True, _scope_override="owner",
        )
        token = runner.agent._TURN_CHANNEL.set(internal)
        try:
            called = json.loads(self.tool(
                "call", request="functions.auth.LogOutRequest",
            ))
        finally:
            runner.agent._TURN_CHANNEL.reset(token)

        receipt = called["receipt"]
        self.assertEqual(receipt["status"], "ok", receipt)
        self.assertEqual(receipt["principal"], runner.agent.PRAXIS_SELF_PRINCIPAL)
        self.assertTrue(receipt["receipt_id"])
        self.assertIsNone(receipt.get("submitted_parameters"))
        self.assertIsNone(receipt.get("serialized_parameters"))
        self.assertIsNone(receipt.get("result"))
        self.assertEqual(len(self.fake.calls), 1)
        self.assertIsInstance(self.fake.calls[0], functions.auth.LogOutRequest)


if __name__ == "__main__":
    unittest.main()
