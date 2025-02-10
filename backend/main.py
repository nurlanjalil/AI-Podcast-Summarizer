from fastapi import FastAPI, UploadFile, HTTPException, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from openai import OpenAI
from pydub import AudioSegment
import os
from dotenv import load_dotenv
import tempfile
import uuid
import logging
import sys
from typing import Dict, Optional
import httpx

# Configure detailed logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()
logger.info("Environment variables loaded")

app = FastAPI(title="Azerbaijani Speech Recognition & Audio Summarizer API")

# Configure CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"]
)
logger.info("CORS configured")

# Configure OpenAI
try:
    http_client = httpx.Client(
        timeout=httpx.Timeout(60.0),
        follow_redirects=True
    )
    
    client = OpenAI(
        api_key=os.getenv('OPENAI_API_KEY'),
        http_client=http_client
    )
    logger.info("OpenAI API key configured successfully")
except Exception as e:
    logger.error(f"Error configuring OpenAI client: {str(e)}")
    logger.error(f"Environment: OPENAI_API_KEY={'*' * 5 if os.getenv('OPENAI_API_KEY') else 'Not Set'}")
    raise Exception(f"OpenAI API key configuration failed: {str(e)}")

# Temporary storage
TEMP_DIR = tempfile.gettempdir()
processing_files: Dict[str, dict] = {}
logger.info(f"Using temporary directory: {TEMP_DIR}")

@app.get("/")
async def root():
    logger.info("Root endpoint accessed")
    return {
        "message": "Welcome to Azerbaijani Speech Recognition & Audio Summarizer API", 
        "endpoints": {
            "/": "This help message",
            "/health": "Health check endpoint",
            "/transcribe-azerbaijani/": "Convert Azerbaijani speech to text",
            "/summarize-audio/": "Transcribe and summarize audio content",
            "/transcribe-live/": "Convert live recorded Azerbaijani speech to text"
        }
    }

@app.post("/transcribe-azerbaijani/")
async def transcribe_azerbaijani(
    file: UploadFile = File(...),
    live_recording: bool = False
):
    """
    Endpoint for Azerbaijani speech-to-text conversion.
    Supports both uploaded files and live recordings.
    """
    logger.info(f"Received Azerbaijani transcription request: {file.filename}")
    logger.debug(f"Live recording: {live_recording}")
    
    try:
        # Process audio file
        temp_path = await save_audio_file(file)
        
        # Transcribe with Whisper (optimized for Azerbaijani)
        raw_transcript = await transcribe_audio(temp_path, "azerbaijani")
        
        # Correct the transcript using GPT-4
        corrected_transcript = await correct_transcript(raw_transcript)
        
        # Cleanup
        if os.path.exists(temp_path):
            os.remove(temp_path)
        
        return JSONResponse({
            "success": True,
            "transcript": corrected_transcript,
            "language": "azerbaijani"
        })
    except Exception as e:
        logger.error(f"Error in Azerbaijani transcription: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Transcription error: {str(e)}")

@app.post("/summarize-audio/")
async def summarize_audio(file: UploadFile = File(...)):
    """
    Endpoint for audio transcription and summarization.
    """
    logger.info(f"Received summarization request: {file.filename}")
    
    try:
        # Process audio file
        temp_path = await save_audio_file(file)
        
        # Transcribe
        raw_transcript = await transcribe_audio(temp_path)
        
        # Correct the transcript
        corrected_transcript = await correct_transcript(raw_transcript)
        
        # Generate summary
        summary = await generate_summary(corrected_transcript)
        
        # Cleanup
        if os.path.exists(temp_path):
            os.remove(temp_path)
        
        return JSONResponse({
            "success": True,
            "transcript": corrected_transcript,
            "summary": summary
        })
    except Exception as e:
        logger.error(f"Error in summarization: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Summarization error: {str(e)}")

@app.post("/transcribe-live/")
async def transcribe_live(file: UploadFile = File(...)):
    """
    Endpoint specifically for live recorded Azerbaijani speech.
    Optimized for real-time audio processing.
    """
    return await transcribe_azerbaijani(file, live_recording=True)

@app.post("/text-to-speech/")
async def text_to_speech(text: Optional[str] = Form(None), text_query: Optional[str] = None):
    """
    Convert text to speech using OpenAI's TTS API.
    Optimized for Azerbaijani language support.
    Accepts text both as form data and query parameter.
    """
    # Get text from either form data or query parameter
    final_text = text or text_query
    
    if not final_text or not final_text.strip():
        raise HTTPException(status_code=400, detail="Text cannot be empty")
    
    logger.info(f"Received TTS request for text: {final_text[:50]}...")
    
    try:
        # Generate speech using OpenAI's TTS
        response = client.audio.speech.create(
            model="tts-1-hd",  # Using HD model for better quality
            voice="nova",  # Nova voice works better for non-English
            input=final_text,
            speed=1.0,
            response_format="mp3"
        )
        
        # Create a unique filename using first few words of text
        text_preview = final_text.split()[:3]
        text_preview = "_".join(text_preview)[:30]
        filename = f"tts_{text_preview}_{uuid.uuid4()[:8]}.mp3"
        temp_path = os.path.join(TEMP_DIR, filename)
        
        # Save the audio file
        response.stream_to_file(temp_path)
        
        # Read the file and return it as a response
        with open(temp_path, "rb") as audio_file:
            content = audio_file.read()
            
        # Cleanup
        os.remove(temp_path)
        
        # Return audio file as response
        headers = {
            "Content-Disposition": f"attachment; filename={filename}",
            "Content-Type": "audio/mpeg",
            "Access-Control-Expose-Headers": "Content-Disposition, Content-Length",
            "Content-Length": str(len(content))
        }
        
        return Response(
            content=content,
            media_type="audio/mpeg",
            headers=headers
        )
        
    except Exception as e:
        logger.error(f"Error in text-to-speech conversion: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500, 
            detail=f"Text-to-speech error: {str(e)}"
        )

async def save_audio_file(file: UploadFile) -> str:
    """
    Save uploaded file and convert to WAV if necessary.
    """
    process_id = str(uuid.uuid4())
    temp_path = os.path.join(TEMP_DIR, f"{process_id}_{file.filename}")
    
    try:
        content = await file.read()
        with open(temp_path, "wb") as buffer:
            buffer.write(content)
        
        # Check audio duration
        audio = AudioSegment.from_file(temp_path)
        duration_seconds = len(audio) / 1000  # Convert milliseconds to seconds
        
        if duration_seconds > 300:  # 5 minutes
            os.remove(temp_path)
            raise HTTPException(
                status_code=400,
                detail="Audio file duration must not exceed 5 minutes"
            )
        
        # Convert to WAV if needed
        if not file.filename.lower().endswith('.wav'):
            wav_path = os.path.join(TEMP_DIR, f"{process_id}.wav")
            audio.export(wav_path, format="wav")
            os.remove(temp_path)
            temp_path = wav_path
            
        return temp_path
    except Exception as e:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise e

async def transcribe_audio(file_path: str, language: Optional[str] = None) -> str:
    """
    Transcribe audio using Whisper API.
    Optimized for Azerbaijani when specified.
    """
    try:
        with open(file_path, "rb") as audio_file:
            response = client.audio.transcriptions.create(
                model="whisper-1",
                file=audio_file,
                response_format="text",
                language="az" if language == "azerbaijani" else language,
                prompt="This is Azerbaijani speech. Please transcribe accurately."
            )
        return response

    except Exception as e:
        logger.error(f"Error in transcription: {str(e)}", exc_info=True)
        raise e

async def generate_summary(transcript: str) -> str:
    """
    Generate a summary of the transcribed text using GPT-4o.
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",  # Updated to use GPT-4o for better performance
            messages=[
                {
                    "role": "system", 
                    "content": """You are an expert in Azerbaijani language and summarization.
                                Your task is to:
                                - Generate a concise and accurate summary of the transcribed text.
                                - Maintain key terminology and cultural context.
                                - Keep the summary in Azerbaijani, preserving the core meaning.
                                
                                Guidelines:
                                - Use clear and natural Azerbaijani language.
                                - Keep the summary brief but informative.
                                - Avoid unnecessary repetition.
                                
                                Return ONLY the summary in Azerbaijani, without additional explanations."""
                },
                {
                    "role": "user", 
                    "content": f"Provide a concise summary of this text in Azerbaijani:\n\n{transcript}"
                }
            ],
            temperature=0.5,  # Lowered temperature for more precise summaries
            max_tokens=300
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Error generating summary: {str(e)}", exc_info=True)
        raise e


async def correct_transcript(transcript: str) -> str:
    """
    Correct the transcribed text using GPT-4o to fix any voice-to-text errors and improve formatting.
    """
    try:
        response = client.chat.completions.create(
            model="gpt-4o",  # Using GPT-4o for the best performance
            messages=[
                {
                    "role": "system", 
                    "content": """You are an expert in Azerbaijani language and text formatting.
                                Your task is to:
                                1. Correct any errors in voice-to-text transcriptions
                                2. Fix grammar, punctuation, and word choice errors
                                3. Format the text properly with paragraphs where appropriate
                                4. Add proper capitalization and sentence structure
                                5. Maintain natural speech flow while improving clarity
                                
                                Guidelines:
                                - Keep the text in Azerbaijani language
                                - Preserve the original meaning and context
                                - Use proper Azerbaijani punctuation rules
                                - Break long sentences into more readable ones
                                - Add paragraphs for better readability
                                
                                Return ONLY the corrected and formatted text, without any explanations."""
                },
                {
                    "role": "user", 
                    "content": f"Correct and format this voice-to-text transcription:\n\n{transcript}"
                }
            ],
            temperature=0.3,  # Lower temperature for more consistent corrections
            max_tokens=1500   # Increased token limit for longer texts
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Error correcting transcript: {str(e)}", exc_info=True)
        raise e


@app.get("/health")
async def health_check():
    """
    Enhanced health check endpoint.
    """
    logger.info("Health check endpoint accessed")
    return {
        "status": "healthy",
        "openai_api": "configured" if client.api_key else "missing",
        "features": {
            "azerbaijani_transcription": True,
            "audio_summarization": True,
            "live_recording": True
        },
        "environment": {
            "python_version": sys.version,
            "temp_dir_writable": os.access(TEMP_DIR, os.W_OK)
        }
    } 