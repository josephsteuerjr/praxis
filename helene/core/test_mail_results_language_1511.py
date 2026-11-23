"""SMTP stages, durable mailbox and UI/frame separation; no network or real mail."""
import asyncio
import copy
from contextlib import ExitStack
import json
import os
from pathlib import Path
import pickle
import smtplib
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import agent
import frame_language as frame
import mail_delivery as delivery
import mailer
import mailroom
import mailroom_bot

RAW='Windows Алекс 中文 {literal} $&'
CFG={'addr':'sender@example.invalid','pw':'fake','smtp_host':'smtp.example.invalid','smtp_port':465}

class MailResults(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(mailroom,'MAILBOX',self.root/'mailbox.json'))
        self.stack.enter_context(patch.object(mailer,'_cfg',return_value=CFG))
        self.stack.enter_context(patch.object(mailer.ssl,'create_default_context',return_value=None))
    def text(self,language,key,**values):
        with frame.use(language):return frame.text('mail.result.'+key,**values)
    def smtp(self,stage='',hook=None,refusals=None):
        events=[];messages=[]
        class SMTP:
            def __init__(self,*args,**kwargs):
                events.append('connect')
                if stage=='connect':raise OSError('raw-connect '+RAW)
            def __enter__(self):return self
            def __exit__(self,*args):
                events.append('close')
                if stage=='close':raise OSError('raw-close '+RAW)
            def login(self,*args):
                events.append('login')
                if stage=='login':raise smtplib.SMTPAuthenticationError(535,b'raw-auth')
            def send_message(self,msg):
                events.append('transfer');messages.append(msg)
                if hook:hook()
                if stage=='refused':raise smtplib.SMTPDataError(550,b'raw-refusal')
                if stage=='unknown':raise smtplib.SMTPServerDisconnected('raw-ambiguous '+RAW)
                return refusals or {}
        return patch.object(mailer,'SMTP_SSL',SMTP),events,messages
    def message(self,subject=RAW):
        return mailroom.ingest([{'from':'Person <person@example.invalid>','subject':subject,
          'date':'2031-04-10','body':RAW,'msg_id':str(len(mailroom._load()))}])[0]['hash']
    def draft(self,subject=RAW):
        h=self.message(subject);self.assertTrue(mailroom.set_draft(h,RAW));return h
    def test_smtp_stages_and_journal_use_frame_under_all16_independent_pairs(self):
        for ui in frame.LANGUAGES:
            for language in frame.LANGUAGES:
                for stage,outcome,kind in [('connect','not_sent','pre_send'),('login','not_sent','pre_send'),
                    ('refused','not_sent','refused'),('unknown','unknown','unknown'),
                    ('','accepted','accepted'),('close','accepted','accepted_close')]:
                    smtp,events,messages=self.smtp(stage)
                    with self.subTest(ui=ui,frame=language,stage=stage),smtp,patch.object(frame,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),patch.object(agent,'tool_journal') as journal:
                        result=agent.tool_send_email('person@example.invalid',RAW,RAW)
                        self.assertIsInstance(result,str);self.assertEqual((result.outcome,result._kind),(outcome,kind))
                        self.assertEqual(events.count('transfer'),int(stage not in {'connect','login'}))
                        expected=result.for_language(language);self.assertEqual(result,expected)
                        suffix={'accepted':'accepted','unknown':'unknown','not_sent':'not_sent'}[outcome]
                        values={'to':'person@example.invalid','subject':RAW}
                        if outcome!='accepted':values['data']=result
                        self.assertEqual(journal.call_args.args[0],self.text(language,'journal_'+suffix,**values))
                        if messages:
                            self.assertEqual(str(messages[0]['Subject']),RAW)
                            self.assertEqual(messages[0].get_content(),RAW+'\n')
    def test_mime_attachments_and_partial_refusal_data_are_literal_with_one_attempt(self):
        path=self.root/(RAW+'.bin');payload=b'\x00\x01literal\xff';path.write_bytes(payload)
        refused={'other@example.invalid':(550,b'raw refusal')}
        smtp,events,messages=self.smtp(refusals=refused)
        with smtp,frame.use('fr'):
            result=mailer.send('person@example.invalid',RAW,RAW,[str(path)])
        self.assertEqual(result.outcome,'accepted');self.assertIn(repr(refused),result)
        self.assertEqual(events.count('transfer'),1);message=messages[0]
        self.assertEqual(str(message['Subject']),RAW)
        self.assertEqual(message.get_body(preferencelist=('plain',)).get_content(),RAW+'\n')
        attachment=list(message.iter_attachments())[0]
        self.assertEqual(attachment.get_filename(),path.name);self.assertEqual(attachment.get_payload(decode=True),payload)
        for language in frame.LANGUAGES:
            rendered=result.for_language(language)
            self.assertIn(self.text(language,'attachment',name=path.name,size=len(payload)),rendered)
            self.assertIn(RAW,rendered);self.assertIn(repr(refused),rendered)
        self.assertEqual(events.count('transfer'),1)
    def test_preflight_rejections_never_connect(self):
        with patch.object(mailer,'SMTP_SSL') as smtp,frame.use('zh'):
            with patch.object(mailer,'configured',return_value=False):
                self.assertEqual(mailer.send('p@x','s','b')._kind,'not_configured')
            self.assertEqual(mailer.send('invalid','s','b')._kind,'bad_address')
            self.assertEqual(mailer.send('p@x','s','b',[str(self.root/'missing')])._kind,'attachment_unreadable')
            path=self.root/'file';path.write_bytes(b'123')
            with patch.object(mailer,'_ATTACH_TOTAL_MAX',2):
                self.assertEqual(mailer.send('p@x','s','b',[str(path)])._kind,'attachment_cap')
            smtp.assert_not_called()
    def test_pin_keeps_smtp_receipt_and_journal_coherent_then_next_call_uses_new_setting(self):
        config={'agent':{'frame_language':'fr'}}
        smtp,events,messages=self.smtp(hook=lambda:config['agent'].update(frame_language='zh'))
        with patch.object(frame,'settings',side_effect=lambda:config),smtp,patch.object(agent,'tool_journal') as journal:
            first=agent.tool_send_email('p@x',RAW,RAW)
            self.assertEqual(first,first.for_language('fr'))
            self.assertEqual(journal.call_args.args[0],self.text('fr','journal_accepted',to='p@x',subject=RAW))
            second=agent.tool_send_email('p@x',RAW,RAW)
            self.assertEqual(second,second.for_language('zh'))
        self.assertEqual(events.count('transfer'),2)
    def test_copy_and_ipc_preserve_frozen_text_and_outcome_ui_recaption_is_explicit(self):
        with frame.use('ru'):
            result=delivery.DeliveryResult('accepted_close',to='p@x',subject=RAW,attached=((RAW,5),),refused='',**delivery.reason_parts('raw '*200,400))
        with frame.use('zh'):
            for restored in (copy.copy(result),copy.deepcopy(result),pickle.loads(pickle.dumps(result))):
                self.assertEqual(str(restored),str(result));self.assertEqual(restored.outcome,'accepted')
        with patch.object(mailer,'send') as send:
            for language in frame.LANGUAGES:
                value=result.for_language(language)
                self.assertIn(self.text(language,'reason_clipped',cap=400),value)
                self.assertIn(self.text(language,'attachment',name=RAW,size=5),value)
                self.assertEqual(value.outcome,'accepted')
            send.assert_not_called()
    def test_caption_fault_after_smtp_acceptance_cannot_erase_outcome_or_cause_resend(self):
        original=frame.text
        def broken(key,**values):
            if key in {'mail.result.accepted','mail.result.journal_accepted'}:raise KeyError('caption')
            return original(key,**values)
        smtp,events,messages=self.smtp()
        with smtp,patch.object(frame,'text',side_effect=broken),patch.object(agent,'tool_journal') as journal:
            result=agent.tool_send_email('p@x',RAW,RAW)
        self.assertEqual(result.outcome,'accepted');self.assertEqual(json.loads(result)['mail_delivery'],'accepted')
        self.assertEqual(events.count('transfer'),1);journal.assert_not_called()
    def test_bare_success_sentences_do_not_mark_sent_but_typed_acceptance_in_every_language_does(self):
        for text in ('Отправлено → p@x','SMTP accepted the message','Sent','Не отправилось'):
            h=self.draft()
            with patch.object(mailer,'send',return_value=text) as send:
                out=mailroom.send_approved(h)
            self.assertEqual(out.outcome,'unknown');self.assertEqual(mailroom.get(h)['status'],'drafted');send.assert_called_once()
            self.assertIn(text,out)
        for language in frame.LANGUAGES:
            h=self.draft();smtp,events,messages=self.smtp()
            with smtp,frame.use(language):out=mailroom.send_approved(h)
            self.assertEqual(out.outcome,'accepted');self.assertEqual(mailroom.get(h)['status'],'sent')
            self.assertEqual(messages[0]['Subject'],'Re: '+RAW)
            with patch.object(mailer,'SMTP_SSL') as again:
                self.assertEqual(mailroom.send_approved(h)._kind,'already_sent');again.assert_not_called()
    def test_real_mailbox_retains_authored_and_ambiguous_old_subjects_drafts_and_read_data(self):
        missing=self.message('');literal=self.message('(без темы)')
        box=mailroom._load();box['old']={'hash':'old','subject':'(без темы)','status':'new'};mailroom._save(box)
        for language in frame.LANGUAGES:
            with frame.use(language):
                self.assertEqual(mailroom.subject_text(mailroom.get(missing)),self.text(language,'no_subject'))
                for h in (literal,'old'):self.assertEqual(mailroom.subject_text(mailroom.get(h)),'(без темы)')
                self.assertIn(self.text(language,'no_subject'),mailroom.index_block())
                self.assertIn(RAW,agent.tool_mail_read(missing))
                out=agent.tool_mail_draft_reply(missing,RAW)
                self.assertEqual(out,self.text(language,'draft_saved',hash=missing,subject=self.text(language,'no_subject')))
                self.assertEqual(mailroom.get(missing)['draft'],RAW)
        self.assertEqual(mailroom.get(missing)['subject'],'(без темы)')
        self.assertTrue(mailroom.get(missing)['subject_missing']);self.assertFalse(mailroom.get(literal)['subject_missing'])
    def test_owner_api_uses_ui_receipt_but_generated_mime_subject_uses_frame_all16_axes(self):
        for ui in frame.LANGUAGES:
            for language in frame.LANGUAGES:
                h=self.draft('');config=self.root/'config.json'
                config.write_text(json.dumps({'lang':ui,'agent':{'frame_language':language}}),encoding='utf-8')
                smtp,events,messages=self.smtp()
                with patch.dict(os.environ,{'HELENE_CONFIG':str(config)}),smtp,patch.object(mailroom_bot,'_owner',return_value=True):
                    response=asyncio.run(mailroom_bot.api_approve(SimpleNamespace(match_info={'hash':h})))
                body=json.loads(response.body);self.assertEqual(response.status,200)
                self.assertEqual((body['ok'],body['outcome']),(True,'accepted'))
                subject='Re: '+self.text(language,'no_subject')
                self.assertEqual(messages[0]['Subject'],subject);self.assertEqual(events.count('transfer'),1)
                self.assertEqual(body['status'],self.text(ui,'accepted',to='person@example.invalid',subject=subject,attachments='',refused=''))
                self.assertEqual(mailroom.get(h)['status'],'sent')
    def test_api_denial_and_ambiguous_result_never_assert_acceptance(self):
        request=SimpleNamespace(match_info={'hash':'literal'})
        with patch.object(mailroom_bot,'_owner',return_value=False),patch.object(mailroom,'send_approved') as send:
            self.assertIn(asyncio.run(mailroom_bot.api_approve(request)).status,(401,403));send.assert_not_called()
        for raw in ('Отправлено → p@x',delivery.DeliveryResult('unknown',to='p@x',subject=RAW,attached=(),refused='',**delivery.reason_parts('raw',400))):
            with patch.object(mailroom_bot,'_owner',return_value=True),patch.object(mailroom,'send_approved',return_value=raw) as send:
                response=asyncio.run(mailroom_bot.api_approve(request))
            self.assertEqual(response.status,502);self.assertEqual(json.loads(response.body)['outcome'],'unknown');send.assert_called_once()
    def test_real_ledger_keeps_unconfirmed_attempt_and_api_returns_502_without_retry(self):
        for stage,outcome in [('connect','not_sent'),('refused','not_sent'),('unknown','unknown'),('close','accepted')]:
            h=self.draft();smtp,events,messages=self.smtp(stage)
            with smtp,patch.object(mailroom_bot,'_owner',return_value=True):
                response=asyncio.run(mailroom_bot.api_approve(SimpleNamespace(match_info={'hash':h})))
            self.assertEqual(json.loads(response.body)['outcome'],outcome)
            self.assertEqual(response.status,200 if outcome=='accepted' else 502)
            self.assertEqual(mailroom.get(h)['status'],'sent' if outcome=='accepted' else 'drafted')
            self.assertEqual(events.count('transfer'),int(stage!='connect'))
    def test_compose_and_fetch_owned_labels_follow_frame_and_draft_is_not_sent(self):
        h=self.message('')
        for ui in frame.LANGUAGES:
            for language in frame.LANGUAGES:
                with patch.object(frame,'settings',return_value={'lang':ui,'agent':{'frame_language':language}}),patch.object(agent.llm,'configured',return_value=True),patch.object(agent,'_voice',return_value=RAW) as voice,patch.object(mailer,'SMTP_SSL') as smtp:
                    self.assertEqual(agent.compose_mail_reply(h),RAW)
                    self.assertEqual(voice.call_args.args[0],self.text(language,'compose_letter',sender='Person <person@example.invalid>',subject=self.text(language,'no_subject'),body=RAW))
                    self.assertEqual(voice.call_args.kwargs['extra_system'],self.text(language,'compose_frame'))
                    self.assertEqual(mailroom.get(h)['status'],'drafted');smtp.assert_not_called()
                    with patch.object(mailer,'fetch',return_value=[]):
                        self.assertEqual(agent.tool_check_email(),self.text(language,'not_received'))
