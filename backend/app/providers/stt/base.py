"""Speech-to-text provider abstraction.

The browser cannot transcribe tab/meeting audio (the Web Speech API only
accepts microphone input). So for interviewer capture we stream the audio
bytes to the backend and transcribe them here. Any future provider
(Deepgram, AssemblyAI, a local Whisper server, or desktop system audio)
only needs to implement this interface.
"""
from abc import ABC, abstractmethod


class STTProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.wav",
        mime_type: str = "audio/wav",
    ) -> str:
        """Return plain text for the given audio clip."""
        raise NotImplementedError

    @property
    def available(self) -> bool:
        """Whether this provider can actually transcribe (has credentials)."""
        return True