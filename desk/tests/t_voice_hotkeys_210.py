import unittest
from localharness.voice_hotkeys import Chord,chord
from localharness.voice_portal import trigger

class VoiceKeys(unittest.TestCase):
    def test_hold_repeat_release_modifiers(self):
        out=[];key=Chord('Ctrl+Alt+Space',out.append)
        self.assertTrue(key.feed('Space',True,{'Ctrl','Alt'}));key.feed('Space',True,{'Ctrl','Alt'})
        key.feed('Alt',False,{'Ctrl'});self.assertTrue(key.feed('Space',False,{'Ctrl'}))
        self.assertEqual(out,['down','up'])
    def test_pause_and_cancel_do_not_start_another_recording(self):
        out=[];key=Chord('Ctrl+Alt+Space',out.append)
        key.feed('Space',True,{'Ctrl','Alt'});key.feed('Shift',True,{'Ctrl','Alt','Shift'})
        key.feed('Escape',True,{'Ctrl','Alt'});key.feed('Escape',False,{'Ctrl','Alt'});key.feed('Space',False,{'Ctrl','Alt'})
        self.assertEqual(out,['down','pause','cancel','up'])
    def test_unrelated_keys_are_not_eaten(self):
        out=[];key=Chord('Ctrl+Alt+Space',out.append)
        self.assertFalse(key.feed('A',True,{'Ctrl'}));self.assertFalse(key.feed('Space',True,{'Alt'}));self.assertEqual(out,[])
    def test_shutdown_cancels_never_sends(self):
        out=[];key=Chord('Ctrl+Alt+Space',out.append);key.feed('Space',True,{'Ctrl','Alt'});key.close();self.assertEqual(out,['down','cancel'])
    def test_validation(self):
        for bad in ('Space','Alt+Alt+Space','Ctrl+Escape',"Ctrl+');bad"):
            with self.assertRaises(ValueError):chord(bad)
        self.assertEqual(trigger(*chord('Ctrl+Alt+Space')),'CTRL+ALT+space')
        self.assertEqual(trigger(*chord('Meta+Enter')),'LOGO+Return')

if __name__=='__main__':unittest.main()
