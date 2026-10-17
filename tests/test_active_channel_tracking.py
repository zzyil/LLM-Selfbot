"""Persistent channel tracking without a panel watch or any live Discord traffic."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as N

from app.utils.discord_servers import ServerAccess
from app.utils.server_channels import ServerChannels
from app.web.routes.chat_routes import apply_event

GUILD, CHANNEL = 1234567890123456789, 987654321098765432


class BackgroundChannels(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'channels.json'
        self.now = 100
        self.perms = N(view_channel=True, read_message_history=True, send_messages=True,
                       send_messages_in_threads=True, manage_threads=False)
        self.guild = N(id=GUILD, name='Test community', me=N(pending=False))
        self.channel = N(id=CHANNEL, name='general', type=0, guild=self.guild,
                         permissions_for=lambda me: self.perms)
        self.client = N(get_channel=lambda cid: self.channel if cid == CHANNEL else None)
        self.access = ServerAccess(self.client, clock=lambda: self.now)
        self.access.activity = ServerChannels(self.client, self.access.channel, 1, path=self.path)
        self.access.activity.configure(GUILD, [str(CHANNEL)], True)

    async def test_saved_selection_listens_without_any_dashboard_visit_and_after_restart(self):
        self.assertFalse(self.access.watching)
        self.assertTrue(self.access.watches(CHANNEL))
        self.now += 7200
        self.assertTrue(self.access.watches(CHANNEL))
        restarted = ServerAccess(self.client)
        restarted.activity = ServerChannels(self.client, restarted.channel, 1, path=self.path)
        self.assertTrue(restarted.watches(CHANNEL))
        self.assertEqual(restarted.active_channels()[0]['id'], f'ch{CHANNEL}')

    async def test_inactive_preview_expires_and_cannot_expand_ai_activity(self):
        self.access.activity.configure(GUILD, [], True)
        await self.access.handle('servers_watch', {'channel_id': str(CHANNEL)})
        self.assertTrue(self.access.watches(CHANNEL))
        self.assertFalse(self.access.activity.allowed(self.channel))
        self.assertEqual(self.access.active_channels(), [])
        self.now += 91
        self.assertFalse(self.access.watches(CHANNEL))

    async def test_revoked_access_and_deselection_stop_tracking(self):
        self.access.record(N(channel=self.channel), {'id': f'ch{CHANNEL}', 'text': 'old'})
        self.perms.view_channel = False
        self.assertFalse(self.access.watches(CHANNEL))
        self.assertEqual(self.access.active_channels(), [])
        self.assertEqual(self.access.latest, {})
        self.perms.view_channel = True
        self.perms.read_message_history = False
        self.assertFalse(self.access.watches(CHANNEL))
        self.perms.read_message_history = True
        self.access.activity.configure(GUILD, [], True)
        self.assertFalse(self.access.watches(CHANNEL))

    async def test_read_only_and_discovered_channels_are_tracked(self):
        self.access.activity.guilds[str(GUILD)]['selected_channel_ids'] = ['42']
        self.access.activity.guilds[str(GUILD)]['discovered'] = {str(CHANNEL): {'message_id': '9'}}
        self.perms.send_messages = False
        row = self.access.active_channels()[0]
        self.assertFalse(row['can_send'])
        self.assertEqual(row['origin'], 'discovered')
        self.assertEqual(row['count'], 0)
        self.assertIsNone(row['state'])
        self.assertTrue(self.access.watches(CHANNEL))

    async def test_background_preview_survives_refresh_and_updates_after_edit_delete(self):
        self.access.record(N(channel=self.channel), {'id': f'ch{CHANNEL}', 'text': 'hi', 'mid': '2',
                                                    'snippet': 'hi', 'ts': 10})
        self.access.update({'t': 'edited', 'uid': f'ch{CHANNEL}', 'mid': '2', 'text': 'changed'})
        self.assertEqual(self.access.active_channels()[0]['text'], 'changed')
        self.access.update({'t': 'deleted', 'uid': f'ch{CHANNEL}', 'mid': '2'})
        self.assertEqual(self.access.active_channels()[0]['text'], '')


class TrackingEvents(unittest.TestCase):
    def setUp(self):
        self.row = {'id': f'ch{CHANNEL}', 'kind': 'server', 'channel_id': str(CHANNEL), 'active': True,
                    'channel_name': 'general', 'guild_id': str(GUILD), 'guild_name': 'Test community',
                    'count': 0, 'state': None}
        self.body = {'users': [{'id': '8', 'count': 1}], 'channels': [], 'active_channels': [self.row]}

    def test_ambient_messages_update_tracking_without_creating_pending_reply_or_contact(self):
        event = {'t': 'channel_msg', 'uid': self.row['id'], 'text': 'ordinary chat', 'mid': '1', 'ts': 5,
                 'author': {'id': '10', 'name': 'Pat'}}
        after = apply_event(self.body, event)
        self.assertEqual(after['channels'], [])
        self.assertEqual(after['users'], self.body['users'])
        self.assertEqual(after['active_channels'][0]['text'], 'ordinary chat')
        self.assertEqual(after['active_channels'][0]['count'], 0)
        self.assertNotIn('text', self.row)
        unknown = apply_event(after, {**event, 'uid': 'ch42'})
        self.assertEqual(len(unknown['active_channels']), 1)

    def test_reply_finishes_without_removing_channel_and_selection_change_is_immediate(self):
        pending = apply_event(self.body, {'t': 'msg', 'uid': self.row['id'], 'kind': 'server',
                                         'text': 'hey', 'mid': '2'})
        writing = apply_event(pending, {'t': 'state', 'uid': self.row['id'],
                                       'state': {'state': 'writing', 'since': 1}})
        self.assertEqual(writing['channels'][0]['state']['state'], 'writing')
        done = apply_event(writing, {'t': 'done', 'uid': self.row['id']})
        self.assertEqual(done['channels'], [])
        self.assertEqual(len(done['active_channels']), 1)
        self.assertEqual(apply_event(done, {'t': 'active_channels', 'channels': []})['active_channels'], [])


if __name__ == '__main__':
    unittest.main()
