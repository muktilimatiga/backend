from fastapi import APIRouter, File, UploadFile, HTTPException
from fastapi.responses import PlainTextResponse
from PIL import Image, ImageOps, ImageFilter, UnidentifiedImageError
import pytesseract
import io
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os

# Configure Tesseract path for Windows
if os.name == 'nt':  # Windows
    tesseract_path = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
    if os.path.exists(tesseract_path):
        pytesseract.pytesseract.tesseract_cmd = tesseract_path

router = APIRouter()

# Create a thread pool for OCR tasks
# Using threads instead of processes to avoid Windows multiprocessing issues
_ocr_executor = ThreadPoolExecutor(max_workers=2)

# Supported text file extensions for direct reading
TEXT_FILE_EXTENSIONS = {'.txt', '.py', '.js', '.ts', '.jsx', '.tsx', '.md', '.json', 
                        '.yaml', '.yml', '.xml', '.html', '.css', '.sh', '.bash',
                        '.java', '.c', '.cpp', '.h', '.go', '.rs', '.rb', '.php'}


def _process_image_ocr(image_bytes: bytes) -> str:
    """
    Heavy OCR processing - runs in separate process to avoid blocking.
    """
    image = Image.open(io.BytesIO(image_bytes))
    
    # --- PREPROCESSING ---
    # Convert RGBA to RGB (remove alpha channel)
    if image.mode == 'RGBA':
        image = image.convert('RGB')
    
    # Invert for dark mode screenshots
    image = ImageOps.invert(image)
    
    # Convert to Grayscale
    image = image.convert('L')
    
    # Upscale 2x (reduced from 3x for better performance)
    image = image.resize((image.width * 2, image.height * 2), Image.Resampling.LANCZOS)
    
    # Sharpen
    image = image.filter(ImageFilter.SHARPEN)

    # Binarize
    image = image.point(lambda x: 0 if x < 180 else 255, '1')
    
    # Extract Text
    custom_config = r'--oem 3 --psm 6'
    text = pytesseract.image_to_string(image, config=custom_config, timeout=15)
    
    return text.strip()


@router.post("/ocr")
async def extract_text(file: UploadFile = File(...)):
    """
    Upload an image and extract text using OCR.
    Runs in a separate process to avoid blocking the event loop.
    """
    if file.content_type not in ["image/jpeg", "image/png", "image/webp"]:
        raise HTTPException(status_code=400, detail="Invalid file type")
        
    try:
        # Read file bytes
        contents = await file.read()
        
        # Run OCR in process pool (non-blocking)
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(_ocr_executor, _process_image_ocr, contents)

        return PlainTextResponse(content=text)

    except UnidentifiedImageError:
        raise HTTPException(status_code=400, detail="Invalid image format")
    except RuntimeError:
        raise HTTPException(status_code=500, detail="Tesseract failed to process the image")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/read-file")
def read_text_file(file: UploadFile = File(...)):
    """
    Read text content directly from a file (no OCR needed).
    Use this when you have the actual text file, not a screenshot.
    
    Supported extensions: .txt, .py, .js, .ts, .md, .json, .yaml, .xml, .html, .css, etc.
    """
    # Check file extension
    filename = file.filename or ""
    ext = '.' + filename.split('.')[-1].lower() if '.' in filename else ''
    
    if ext not in TEXT_FILE_EXTENSIONS:
        raise HTTPException(
            status_code=400, 
            detail=f"Unsupported file type. Supported: {', '.join(sorted(TEXT_FILE_EXTENSIONS))}"
        )
    
    try:
        contents = file.file.read()
        text = contents.decode('utf-8')
        
        return {
            "filename": file.filename,
            "text": text,
            "lines": len(text.splitlines()),
            "status": "success"
        }
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File is not valid UTF-8 text")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))