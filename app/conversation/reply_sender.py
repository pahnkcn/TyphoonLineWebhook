"""
Reply Sender module for the 'ใจดี' chatbot.
Handles unified reply/push message splitting and batching for LINE API.
"""

import logging
import re
import time
from typing import List, Optional, Any
from dataclasses import dataclass
from enum import Enum


class SendResult(Enum):
    """Result type for send operations."""
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


@dataclass
class SendResponse:
    """Response from send operation."""
    result: SendResult
    messages_sent: int = 0
    messages_failed: int = 0
    error: Optional[str] = None


class ReplySender:
    """
    Handles sending messages to LINE users with proper batching and splitting.
    
    Responsibilities:
    - Split long messages into segments
    - Batch messages (max 5 per LINE API call)
    - Handle reply vs push message logic
    - Manage rate limiting between batches
    """
    
    MAX_MESSAGES_PER_BATCH = 5
    BATCH_DELAY_SECONDS = 0.5
    MAX_MESSAGE_LENGTH = 5000  # LINE message limit
    
    def __init__(self, line_bot_api: Any):
        """
        Initialize the reply sender.
        
        Args:
            line_bot_api: LINE Bot API instance
        """
        self.line_bot_api = line_bot_api
    
    def send_response(
        self,
        user_id: str,
        text: str,
        reply_token: Optional[str] = None,
    ) -> SendResponse:
        """
        Send a response to a user, handling splitting and batching.
        
        Strategy:
        1. Split text into segments
        2. If reply_token is available, use reply for first batch (up to 5 messages)
        3. Use push for remaining messages
        
        Args:
            user_id: LINE User ID
            text: Response text to send
            reply_token: Optional reply token for immediate reply
            
        Returns:
            SendResponse with result and statistics
        """
        if not text:
            return SendResponse(result=SendResult.SUCCESS, messages_sent=0)
        
        # Split text into segments
        segments = self._split_text(text)
        
        if not segments:
            return SendResponse(result=SendResult.SUCCESS, messages_sent=0)
        
        # Convert to LINE message objects
        from linebot.models import TextSendMessage
        messages = [TextSendMessage(text=segment) for segment in segments]
        
        messages_sent = 0
        messages_failed = 0
        to_push = messages
        
        # Try to use reply token for first batch
        if reply_token:
            reply_batch = messages[:self.MAX_MESSAGES_PER_BATCH]
            try:
                if reply_batch:
                    payload = reply_batch if len(reply_batch) > 1 else reply_batch[0]
                    self.line_bot_api.reply_message(reply_token, payload)
                    messages_sent += len(reply_batch)
                    to_push = messages[self.MAX_MESSAGES_PER_BATCH:]
            except Exception as exc:
                logging.warning(f"Reply message failed for user {user_id}: {exc}")
                # Fall back to push for all messages
                to_push = messages
        
        # Send remaining messages via push
        push_result = self._send_push_batches(user_id, to_push)
        messages_sent += push_result.messages_sent
        messages_failed += push_result.messages_failed
        
        # Determine overall result
        if messages_failed == 0:
            result = SendResult.SUCCESS
        elif messages_sent > 0:
            result = SendResult.PARTIAL_SUCCESS
        else:
            result = SendResult.FAILED
        
        return SendResponse(
            result=result,
            messages_sent=messages_sent,
            messages_failed=messages_failed,
        )
    
    def send_push_message(
        self,
        user_id: str,
        text: str,
    ) -> SendResponse:
        """
        Send a push message to a user (no reply token).
        
        Args:
            user_id: LINE User ID
            text: Message text to send
            
        Returns:
            SendResponse with result and statistics
        """
        return self.send_response(user_id, text, reply_token=None)
    
    def send_multiple_messages(
        self,
        user_id: str,
        texts: List[str],
        reply_token: Optional[str] = None,
    ) -> SendResponse:
        """
        Send multiple separate messages to a user.
        
        Args:
            user_id: LINE User ID
            texts: List of message texts to send
            reply_token: Optional reply token for immediate reply
            
        Returns:
            SendResponse with result and statistics
        """
        if not texts:
            return SendResponse(result=SendResult.SUCCESS, messages_sent=0)
        
        # Flatten all texts into segments
        all_segments = []
        for text in texts:
            segments = self._split_text(text)
            all_segments.extend(segments)
        
        if not all_segments:
            return SendResponse(result=SendResult.SUCCESS, messages_sent=0)
        
        # Convert to LINE message objects
        from linebot.models import TextSendMessage
        messages = [TextSendMessage(text=segment) for segment in all_segments]
        
        messages_sent = 0
        messages_failed = 0
        to_push = messages
        
        # Try to use reply token for first batch
        if reply_token:
            reply_batch = messages[:self.MAX_MESSAGES_PER_BATCH]
            try:
                if reply_batch:
                    payload = reply_batch if len(reply_batch) > 1 else reply_batch[0]
                    self.line_bot_api.reply_message(reply_token, payload)
                    messages_sent += len(reply_batch)
                    to_push = messages[self.MAX_MESSAGES_PER_BATCH:]
            except Exception as exc:
                logging.warning(f"Reply message failed for user {user_id}: {exc}")
                to_push = messages
        
        # Send remaining messages via push
        push_result = self._send_push_batches(user_id, to_push)
        messages_sent += push_result.messages_sent
        messages_failed += push_result.messages_failed
        
        # Determine overall result
        if messages_failed == 0:
            result = SendResult.SUCCESS
        elif messages_sent > 0:
            result = SendResult.PARTIAL_SUCCESS
        else:
            result = SendResult.FAILED
        
        return SendResponse(
            result=result,
            messages_sent=messages_sent,
            messages_failed=messages_failed,
        )
    
    def _split_text(self, text: str) -> List[str]:
        """
        Split text into segments suitable for LINE messages.
        
        Splitting strategy:
        1. Split on double newlines or bullet points
        2. Ensure each segment is under MAX_MESSAGE_LENGTH
        3. Preserve meaningful content boundaries
        
        Args:
            text: Text to split
            
        Returns:
            List of text segments
        """
        if not text:
            return []
        
        text = text.strip()
        if not text:
            return []
        
        # Split on double newlines or bullet points
        raw_segments = re.split(r"\n{2,}|•", text)
        segments = [seg.strip() for seg in raw_segments if seg.strip()]
        
        # If no segments from splitting, use original text
        if not segments:
            segments = [text]
        
        # Further split any segments that are too long
        final_segments = []
        for segment in segments:
            if len(segment) <= self.MAX_MESSAGE_LENGTH:
                final_segments.append(segment)
            else:
                # Split long segments at sentence boundaries
                sub_segments = self._split_long_segment(segment)
                final_segments.extend(sub_segments)
        
        return final_segments
    
    def _split_long_segment(self, text: str) -> List[str]:
        """
        Split a long segment into smaller pieces at sentence boundaries.
        
        Args:
            text: Long text to split
            
        Returns:
            List of smaller segments
        """
        segments = []
        current = ""
        
        # Split at Thai and English sentence endings
        sentences = re.split(r'(?<=[.!?。\n])\s*', text)
        
        for sentence in sentences:
            if not sentence.strip():
                continue
            
            if len(current) + len(sentence) + 1 <= self.MAX_MESSAGE_LENGTH:
                current = current + " " + sentence if current else sentence
            else:
                if current:
                    segments.append(current.strip())
                
                # If single sentence is too long, force split
                if len(sentence) > self.MAX_MESSAGE_LENGTH:
                    words = sentence.split()
                    current = ""
                    for word in words:
                        if len(current) + len(word) + 1 <= self.MAX_MESSAGE_LENGTH:
                            current = current + " " + word if current else word
                        else:
                            if current:
                                segments.append(current.strip())
                            current = word
                else:
                    current = sentence
        
        if current:
            segments.append(current.strip())
        
        return segments
    
    def _send_push_batches(
        self,
        user_id: str,
        messages: List[Any],
    ) -> SendResponse:
        """
        Send messages via push in batches.
        
        Args:
            user_id: LINE User ID
            messages: List of LINE message objects
            
        Returns:
            SendResponse with result and statistics
        """
        if not messages:
            return SendResponse(result=SendResult.SUCCESS, messages_sent=0)
        
        messages_sent = 0
        messages_failed = 0
        
        for index in range(0, len(messages), self.MAX_MESSAGES_PER_BATCH):
            batch = messages[index:index + self.MAX_MESSAGES_PER_BATCH]
            if not batch:
                continue
            
            try:
                payload = batch if len(batch) > 1 else batch[0]
                self.line_bot_api.push_message(user_id, payload)
                messages_sent += len(batch)
                
                # Add delay between batches to avoid rate limiting
                if index + self.MAX_MESSAGES_PER_BATCH < len(messages):
                    time.sleep(self.BATCH_DELAY_SECONDS)
                    
            except Exception as e:
                logging.error(f"Push message failed for user {user_id}: {e}")
                messages_failed += len(batch)
        
        # Determine result
        if messages_failed == 0:
            result = SendResult.SUCCESS
        elif messages_sent > 0:
            result = SendResult.PARTIAL_SUCCESS
        else:
            result = SendResult.FAILED
        
        return SendResponse(
            result=result,
            messages_sent=messages_sent,
            messages_failed=messages_failed,
        )
    
    def send_processing_status(
        self,
        user_id: str,
        reply_token: str,
        processing_messages: List[str],
    ) -> bool:
        """
        Send a processing status message.
        
        Args:
            user_id: LINE User ID
            reply_token: Reply token for immediate reply
            processing_messages: List of possible processing messages
            
        Returns:
            True if sent successfully, False otherwise
        """
        from random import choice
        from linebot.models import TextSendMessage
        
        try:
            processing_message = choice(processing_messages)
            self.line_bot_api.reply_message(
                reply_token,
                TextSendMessage(text=processing_message)
            )
            return True
        except Exception as e:
            logging.error(f"Failed to send processing status: {e}")
            return False
