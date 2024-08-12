import requests
import json
import time
import asyncio
import websockets
from pydub import AudioSegment
from pydub.playback import play
from datetime import datetime, timezone
import certifi
import wave
import base64
import io
import copy
import simpleaudio as sa

from datetime import datetime, timezone

import ssl
ssl_context = ssl.create_default_context(cafile=certifi.where())

OPENAI_API_LLM_BASE="https://api.openai.com/v1"
OPENAI_API_LLM_KEY=""
OPENAI_API_LLM_TYPE="open_ai"
OPENAI_API_LLM_VERSION="None"
OPENAI_GPT_MAX_TOKEN=500
OPENAI_CHAT_COMPLETION_URL="https://api.openai.com/v1/chat/completions"
GPT_REQ_TIMEOUT=40
OPENAI_LLM_ENGINE="GenAi-VCA-1"
LLM_PROMPT_JSON_RESPONE = "(Unspoken NOTE: Don't forget to respond in a plain non json string containing answer to user question (separate sentences properly with comma, question mark, full stop, etc )"
LLM_PROMPT = "You are a model that answers user questions, given messages of user answer them. Respond concisely in 2-3 sentences in hindi"

SMALLEST_API_KEY=""
SMALLEST_API_URL= f"wss://call-dev.smallest.ai/invocations_streaming?token={SMALLEST_API_KEY}"

llm_creds = {
        'api_key': OPENAI_API_LLM_KEY,
        'api_base': OPENAI_API_LLM_BASE,
        'api_type': OPENAI_API_LLM_TYPE,
        'api_version': OPENAI_API_LLM_VERSION,
        'engine': OPENAI_LLM_ENGINE
}

def get_llm_headers():
    return {
        'Authorization': f'Bearer {llm_creds["api_key"]}',
        'Content-Type': 'application/json'
    }
def get_llm_chat_messages():
    messages=[
        {
        "content": LLM_PROMPT,
        "role": "system"
        },
        {
        "content": "Tell me about Meesho",
        "role": "user"
        }
    ]
    last_user_msg = None
    for chat_msg in messages:
        if chat_msg["role"] == "user":
            last_user_msg = chat_msg
    if last_user_msg:
        last_user_msg["content"] += LLM_PROMPT_JSON_RESPONE
    return messages

def get_complete_phrases(llm_content, min_size=20):
    """
    This function identifies complete sentences in the given llm_content based on specified separators (., ?, ,).
    It ensures that the chunk has at least `min_size` characters before yielding it.
    If the chunk does not meet the minimum size, it returns None and the full content to accumulate more content.
    """

    separators = ['.', '?', ',', '\n','।']
    
    last_separator = -1
    for sep in separators:
        last_separator = max(last_separator, llm_content.rfind(sep))

    if last_separator == -1:
        return None, llm_content

    to_yield = llm_content[:last_separator + 1].strip()
    remaining_content = llm_content[last_separator + 1:].strip()

    while len(to_yield) < min_size and remaining_content:
        next_separator = -1
        for sep in separators:
            next_separator = max(next_separator, remaining_content.find(sep))
        
        if next_separator == -1:
            break
        
        to_yield += ' ' + remaining_content[:next_separator + 1].strip()
        remaining_content = remaining_content[next_separator + 1:].strip()

    if len(to_yield) < min_size:
        return None, llm_content

    return to_yield, remaining_content

async def stream_llm_output():
    messages = get_llm_chat_messages()
    llm_request_data = {
        'model': 'gpt-4o-mini-2024-07-18',
        'temperature': 0.3,
        'messages': messages,
        'max_tokens': OPENAI_GPT_MAX_TOKEN,
        'response_format': { "type": "text" },
        'stream': True
    }
    headers = get_llm_headers()

    with requests.post(OPENAI_CHAT_COMPLETION_URL, headers=headers, json=llm_request_data, stream=True) as response:
        response.raise_for_status()
        llm_content=""
        for chunk in response.iter_lines(decode_unicode=True):
            if chunk and chunk.startswith('data: '):
                chunk_data = chunk[6:]
                if chunk_data.strip() == '[DONE]':
                    break
                json_data = json.loads(chunk_data)
                content = json_data['choices'][0]['delta'].get('content', '')
                if content:
                    llm_content+=content
                complete_phrase, llm_content = get_complete_phrases(llm_content)
                if complete_phrase:
                    yield complete_phrase.replace('\n',' ').strip()
                else:
                    continue

        if(len(llm_content)>0):
            llm_content=llm_content.replace('\n',' ').strip()
            yield llm_content

async def awaaz_streaming_llm(url):
    wav_audio_bytes = b''
    timeout = 1
    first = False
    CLOSE_CONNECTION_TIMEOUT = 500

    start_time = time.time()
    prev_time = time.time()
    total_chunks=0
    llm_total_time = 0
    total_tts_time=0
    llm_response=""

    total_chunks_tts=0
    total_time_tts_chunks=0
    request_payloads=[]


    try:
        async with websockets.connect(url, ssl=ssl_context) as websocket:
            async for llm_chunk in stream_llm_output():
                llm_chunk_time = time.time()
                llm_total_time+= (llm_chunk_time - prev_time)
                total_chunks+=1
                llm_response+=llm_chunk

                payload = {
                    'text': llm_chunk,
                    'voice_id': 'anuja_indian_female',
                    'add_wav_header': False,
                    'speed': 1.3,
                    'keep_ws_open': True
                }
                request_payloads.append(payload)
                data = json.dumps(payload)
                await websocket.send(data)

                response = b''
                prev_chunk_time = time.time()
                while True:
                    try:
                        part= b''
                        response_part = await asyncio.wait_for(websocket.recv(), timeout=timeout)

                        total_chunks_tts+=1
                        current_time_tts=time.time()
                        total_time_tts_chunks+= (current_time_tts-prev_chunk_time)
                        prev_chunk_time=current_time_tts
                        part+=response_part

                        play_audio_from_bytes(part)
                        

                        response += response_part
                    except asyncio.TimeoutError:
                        break
                
                prev_time=time.time()
                diff = prev_time-llm_chunk_time
                total_tts_time+=diff
                wav_audio_bytes+=response

            await websocket.close()

            wav_audio_bytes = encode_audio_common_wav(wav_audio_bytes)
            with open("final_audio_output.wav", "wb") as f:
                f.write(wav_audio_bytes)

    except websockets.exceptions.ConnectionClosed as e:
        if e.code == 1000:
            print("Connection closed normally (code 1000).")
        else:
            print(f"Connection closed with code {e.code}: {e.reason}")
    except Exception as e:
        print(f"Exception occurred: {e}")

    finally:
        print(f"LLM Response : {llm_response}")
        print(f"Average time between llm chunks - {(llm_total_time/total_chunks):.4f}")
        print(f"Average response time for tts chunk (part of a llm chunk) - {(total_time_tts_chunks/total_chunks_tts):.4f}")
        print(f"Average response time by tts for the llm chunk - {(total_tts_time/total_chunks):.4f}")
        print(f"Total processing time: {(time.time()-start_time):.4f} seconds")
        print(f"Request payloads - {request_payloads}")
        # print("Connection closed")
        return


async def main():
    uri = SMALLEST_API_URL
    await awaaz_streaming_llm(uri)

def encode_audio_common_wav(frame_input, sample_rate=24000, sample_width=2, channels=1):
    """Ensure the WAV file is encoded with standard settings"""
    audio = AudioSegment(
        data=frame_input,
        sample_width=sample_width,
        frame_rate=sample_rate,
        channels=channels
    )

    wav_buf = io.BytesIO()
    audio.export(wav_buf, format="wav")
    wav_buf.seek(0)
    return wav_buf.read()

def play_audio_from_bytes(audio_bytes):
    play_obj = sa.play_buffer(audio_bytes, num_channels=1, bytes_per_sample=2, sample_rate=24000)
    play_obj.wait_done()

if __name__ == "__main__":
    asyncio.run(main())
