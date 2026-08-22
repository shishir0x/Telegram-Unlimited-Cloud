import asyncio
from typing import Dict, Union
from pyrogram import Client, utils, raw
from .file_properties import get_file_ids
from pyrogram.session import Session, Auth
from pyrogram.errors import AuthBytesInvalid
from pyrogram.file_id import FileId, FileType, ThumbnailSource
from utils.logger import Logger

logger = Logger(__name__)


class ByteStreamer:
    def __init__(self, client: Client):
        self.clean_timer = 30 * 60
        self.client: Client = client
        self.cached_file_ids: Dict[int, FileId] = {}
        asyncio.create_task(self.clean_cache())

    async def get_file_properties(self, channel, message_id: int) -> FileId:
        if message_id not in self.cached_file_ids:
            await self.generate_file_properties(channel, message_id)
        return self.cached_file_ids[message_id]

    async def generate_file_properties(self, channel, message_id: int) -> FileId:
        file_id = await get_file_ids(self.client, channel, message_id)
        if not file_id:
            raise Exception("FileNotFound")
        self.cached_file_ids[message_id] = file_id
        return self.cached_file_ids[message_id]

    async def generate_media_session(self, client: Client, file_id: FileId) -> Session:
        """
        Generates the media session for the DC that contains the media file.
        This is required for getting the bytes from Telegram servers.
        """
        try:
            return await client.get_session(file_id.dc_id, is_media=True)
        except Exception as e:
            logger.debug(f"Media session (is_media=True) for DC {file_id.dc_id}: {e}. Retrying standard session.")
            return await client.get_session(file_id.dc_id)

    @staticmethod
    async def get_location(
        file_id: FileId,
    ) -> Union[
        raw.types.InputPhotoFileLocation,
        raw.types.InputDocumentFileLocation,
        raw.types.InputPeerPhotoFileLocation,
    ]:
        """
        Returns the file location for the media file.
        """
        file_type = file_id.file_type

        if file_type == FileType.CHAT_PHOTO:
            if file_id.chat_id > 0:
                peer = raw.types.InputPeerUser(
                    user_id=file_id.chat_id, access_hash=file_id.chat_access_hash
                )
            else:
                if file_id.chat_access_hash == 0:
                    peer = raw.types.InputPeerChat(chat_id=-file_id.chat_id)
                else:
                    peer = raw.types.InputPeerChannel(
                        channel_id=utils.get_channel_id(file_id.chat_id),
                        access_hash=file_id.chat_access_hash,
                    )

            location = raw.types.InputPeerPhotoFileLocation(
                peer=peer,
                volume_id=file_id.volume_id,
                local_id=file_id.local_id,
                big=file_id.thumbnail_source == ThumbnailSource.CHAT_PHOTO_BIG,
            )
        elif file_type == FileType.PHOTO:
            location = raw.types.InputPhotoFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        else:
            location = raw.types.InputDocumentFileLocation(
                id=file_id.media_id,
                access_hash=file_id.access_hash,
                file_reference=file_id.file_reference,
                thumb_size=file_id.thumbnail_size,
            )
        return location

    async def yield_file(
        self,
        file_id: FileId,
        offset: int,
        first_part_cut: int,
        last_part_cut: int,
        part_count: int,
        chunk_size: int,
    ):
        """
        Custom generator that yields the bytes of the media file.
        """
        client = self.client
        logger.debug(f"Starting to yielding file with client.")
        media_session = await self.generate_media_session(client, file_id)

        current_part = 1
        location = await self.get_location(file_id)

        try:
            while current_part <= part_count:
                try:
                    r = await asyncio.wait_for(
                        media_session.invoke(
                            raw.functions.upload.GetFile(
                                location=location, offset=offset, limit=chunk_size
                            )
                        ),
                        timeout=20.0,
                    )
                except Exception as e:
                    logger.warning(
                        f"GetFile timeout/error on DC {file_id.dc_id}: {e}. Reconnecting media session..."
                    )
                    client.media_sessions.pop(file_id.dc_id, None)
                    try:
                        await media_session.stop()
                    except Exception:
                        pass
                    media_session = await self.generate_media_session(client, file_id)
                    r = await asyncio.wait_for(
                        media_session.invoke(
                            raw.functions.upload.GetFile(
                                location=location, offset=offset, limit=chunk_size
                            )
                        ),
                        timeout=25.0,
                    )

                if isinstance(r, raw.types.upload.File):
                    chunk = r.bytes
                    if not chunk:
                        break
                    elif part_count == 1:
                        yield chunk[first_part_cut:last_part_cut]
                    elif current_part == 1:
                        yield chunk[first_part_cut:]
                    elif current_part == part_count:
                        yield chunk[:last_part_cut]
                    else:
                        yield chunk

                    current_part += 1
                    offset += chunk_size
                else:
                    break
        except (asyncio.CancelledError, GeneratorExit):
            pass
        except Exception as e:
            logger.error(f"Error streaming file chunks: {e}")
            client.media_sessions.pop(file_id.dc_id, None)
        finally:
            logger.debug(f"Finished yielding file with {current_part} parts.")

    async def clean_cache(self) -> None:
        """
        function to clean the cache to reduce memory usage
        """
        while True:
            await asyncio.sleep(self.clean_timer)
            self.cached_file_ids.clear()
            logger.debug("Cleaned the cache")
