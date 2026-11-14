# Incoming voice messages

A voice-message marker without text does not mean the instruction was
read. Check the transcript in the available event and, if needed,
`events.jsonl`. Find the original artifact from the actual message path,
such as `memory/runs/<run>/artifacts/`, rather than guess its filename.

When the audio component is available, use `media_audio.transcribe(path)`
or the current transcription tool. Assume neither installed Whisper,
a fixed 25-second duration nor identical executor limits. Manage a long
operation through supported identity, deadline and polling; leave no
unowned background process.

Read the result and perform the understood authorized task. Save important
information with its source in the proper private context. On failure,
say that no transcript was obtained and request text or an available
retry route. Do not replace unheard content with a guess or silence.
