"""Model-visible voice contracts. Data only; no capability or model initialization."""

TOOLS = [
    {'name':'voice_backend',
     'description':'Inspect the actual speech provider, Fish API balance and voices, or explicitly select a Fish voice/model or enable/pause speech. This never generates audio or sends a message. Fish delivery directions are performance cues rather than spoken text; Drama 3 supports Russian directions and word-level delivery tags. A remote cancellation cannot be confirmed by closing a local HTTP response.',
     'input_schema':{'type':'object','properties':{
         'action':{'type':'string','enum':['status','voices','select','enable'],'description':'Read actual status or catalogue, explicitly choose a Fish voice/model, or enable/pause speech.'},
         'voice_id':{'type':'string','description':'Exact voice ID from the Fish catalogue; required for select.'},'model':{'type':'string','description':'Fish model, e.g. drama-3 for expressive direction or s2.1-pro.'},
         'enabled':{'type':'boolean','description':'Whether speech is enabled after select or enable; false pauses speech.'},
         'page':{'type':'integer','minimum':1,'description':'One-based voice catalogue page.'},
         'language':{'type':'string','description':'Catalogue language filter, e.g. ru for Russian voices.'}},'required':['action']}},
    {'name': 'generate_voice',
     'description': (
         'Start background speech generation and return its generation ID immediately. '
         'Choose the exact words to voice. Optional delivery is a free-form performance direction for Fish: '
         'emotion, intent, pacing, emphasis, pauses, laughter or whisper; Russian directions work with Drama 3. '
         'Inline [directions] apply where placed; Drama 3 also supports <whisper>, <emphasis>, <soft>, <fast>, '
         '<slow>, <stress>, <forceful> around selected words. Use voice_backend to inspect/select provider and voice. '
         'This creates an audio result and sends nothing to Telegram. '
         'While it runs, work may continue, a separate text reply may be sent with reply, or the current turn may end. '
         'Ending the turn does not cancel generation. A result or failure may arrive later as a separate tool event, '
         'including after the turn ends; a confirmed result may never arrive. There is no need to keep the turn open '
         'or poll continuously. A result event does not itself require a chat reply. Use send_voice as the separate '
         'send action. Generation has no imposed overall deadline or automatic result expiry.'),
     'input_schema': {'type': 'object', 'properties': {
         'text': {'type': 'string', 'description': 'Speech script; optional inline Fish performance cues are not spoken words.'},
         'delivery': {'type': 'string', 'description': 'Optional free-form direction for the performance, separate from spoken words. No fixed emotion list.'},
     }, 'required': ['text']}},
    {'name': 'voice_generation',
     'description': (
         'Inspect a background speech generation, read its saved result, or request cancellation by generation ID. '
         'Inspection returns the currently known state without waiting for completion. States distinguish queued, '
         'running, paused, ready, failed, cancellation requested, cancelled, and an unknown outcome. '
         'A cancellation request is separate from confirmed cancellation. Ending the original turn does not cancel '
         'generation. Missing confirmation proves neither success nor delivery. This tool sends no chat message.'),
     'input_schema': {'type': 'object', 'properties': {
         'action': {'type': 'string', 'enum': ['status', 'result', 'cancel'],
                    'description': 'Read current status/result, or request cancellation.'},
         'generation_id': {'type': 'string', 'description': 'ID returned by generate_voice.'},
     }, 'required': ['action', 'generation_id']}},
    {'name': 'send_voice',
     'description': (
         'Request delivery of a generation as a Telegram voice note to its originating chat. '
         'This is the separate send action: starting generation, finishing it, and receiving a tool result '
         'do not themselves send anything. Delivery may be requested while generation is still running; '
         'encoding and upload stream as audio becomes available. The turn may end while delivery is pending. '
         'Telegram publishes the voice-note message after upload is finalized. Sending is confirmed separately '
         'by Telegram accepting the message; queued, ready, and uploaded do not mean sent. '
         'A late result event returns to the originating chat context, where this action is available. '
         'Use send_media on a saved ready result for a deliberate delivery to another recipient.'),
     'input_schema': {'type': 'object', 'properties': {
         'generation_id': {'type': 'string', 'description': 'Generation whose audio to send.'},
         'caption': {'type': 'string', 'description': 'Optional caption accompanying the voice note.'},
     }, 'required': ['generation_id']}},
]

RESULT_INTRO = (
    'A background speech generation has reached the state shown below. This is a result of an earlier '
    'tool call, not a new message from the other person. The original turn may already have ended. '
    'The result itself sends nothing. A separate send_voice request may already be pending; its delivery '
    'receipt is independent of this generation result. If no send was requested, the audio has not been sent. '
    'Choose whether any further action is needed. A chat reply is optional. An unknown outcome is not success, '
    'delivery, or confirmed cancellation.\n'
)
