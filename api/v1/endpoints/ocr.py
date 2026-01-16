from fastapi import APIRouter, File, UploadFile, HTTPException
from fastapi.responses import PlainTextResponse
from PIL import Image, ImageOps, ImageFilter, UnidentifiedImageError
import pytesseract
import io
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
import shutil
import subprocess
import cv2
import numpy as np

# Configure Tesseract path based on OS
def _configure_tesseract():
    """Configure Tesseract path and verify installation."""
    if os.name == 'nt':  # Windows
        tesseract_path = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
        if os.path.exists(tesseract_path):
            pytesseract.pytesseract.tesseract_cmd = tesseract_path
            return True
    else:  # Linux/Unix
        # Check common Linux paths
        linux_paths = [
            '/usr/bin/tesseract',
            '/usr/local/bin/tesseract',
            '/opt/tesseract/bin/tesseract',
        ]
        for path in linux_paths:
            if os.path.exists(path):
                pytesseract.pytesseract.tesseract_cmd = path
                return True
        
        # Try to find tesseract in PATH
        tesseract_in_path = shutil.which('tesseract')
        if tesseract_in_path:
            pytesseract.pytesseract.tesseract_cmd = tesseract_in_path
            return True
    
    return False

def _verify_tesseract():
    """Verify Tesseract is working and return version info."""
    try:
        version = pytesseract.get_tesseract_version()
        print(f"[OCR] Tesseract version: {version}")
        return True
    except pytesseract.TesseractNotFoundError:
        print("[OCR] ERROR: Tesseract not found!")
        print("[OCR] Install with: sudo apt-get install tesseract-ocr")
        return False
    except Exception as e:
        print(f"[OCR] ERROR verifying Tesseract: {e}")
        return False

# Configure and verify on startup
_tesseract_configured = _configure_tesseract()
_tesseract_available = _verify_tesseract()

router = APIRouter()

# Create a thread pool for OCR tasks
# Using threads instead of processes to avoid Windows multiprocessing issues
_ocr_executor = ThreadPoolExecutor(max_workers=2)

# Supported text file extensions for direct reading
TEXT_FILE_EXTENSIONS = {'.txt', '.py', '.js', '.ts', '.jsx', '.tsx', '.md', '.json', 
                        '.yaml', '.yml', '.xml', '.html', '.css', '.sh', '.bash',
                        '.java', '.c', '.cpp', '.h', '.go', '.rs', '.rb', '.php'}


def _process_image_ocr(image_bytes: bytes, lang: str = 'eng') -> str:
    """
    Heavy OCR processing with optimized preprocessing for accuracy.
    
    Args:
        image_bytes: Raw image bytes
        lang: Tesseract language code (e.g., 'eng', 'ind', 'eng+ind')
    """
    image = Image.open(io.BytesIO(image_bytes))
    
    # --- PREPROCESSING ---
    # Convert RGBA to RGB (remove alpha channel)
    if image.mode == 'RGBA':
        background = Image.new('RGB', image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[3])
        image = background
    
    # Convert to Grayscale first
    image = image.convert('L')
    
    # Smart invert: only invert if image is dark (dark mode screenshots)
    img_array = np.array(image)
    avg_brightness = np.mean(img_array)
    if avg_brightness < 128:
        image = ImageOps.invert(image)
        img_array = np.array(image)
    
    # Upscale for better OCR (Tesseract works best at ~300 DPI)
    # Scale 3x for small text, 2x for normal text
    scale_factor = 3 if min(image.size) < 500 else 2
    new_size = (image.width * scale_factor, image.height * scale_factor)
    image = image.resize(new_size, Image.Resampling.LANCZOS)
    img_array = np.array(image)
    
    # Denoise using median filter (removes salt-and-pepper noise)
    img_array = cv2.medianBlur(img_array, 3)
    
    # Adaptive thresholding (much better than fixed threshold)
    # This handles varying lighting conditions across the image
    img_array = cv2.adaptiveThreshold(
        img_array, 
        255, 
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
        cv2.THRESH_BINARY, 
        11,  # Block size
        2    # C constant
    )
    
    # Optional: Morphological operations to clean up
    kernel = np.ones((1, 1), np.uint8)
    img_array = cv2.morphologyEx(img_array, cv2.MORPH_CLOSE, kernel)
    
    # Convert back to PIL Image
    image = Image.fromarray(img_array)
    
    # --- OCR CONFIG ---
    # --oem 3: Use LSTM + legacy engine (best accuracy)
    # --psm 6: Assume uniform block of text
    custom_config = r'--oem 3 --psm 6'
    
    text = pytesseract.image_to_string(
        image, 
        lang=lang,
        config=custom_config, 
        timeout=30
    )
    
    return text.strip()


@router.post("/ocr")
async def extract_text(file: UploadFile = File(...)):
    """
    Upload an image and extract text using OCR.
    Runs in a separate process to avoid blocking the event loop.
    """
    # Check if Tesseract is available
    if not _tesseract_available:
        raise HTTPException(
            status_code=503, 
            detail="Tesseract OCR is not installed. Install with: sudo apt-get install tesseract-ocr"
        )
    
    if file.content_type not in ["image/jpeg", "image/png", "image/webp"]:
        raise HTTPException(status_code=400, detail="Invalid file type")
        
    try:
        # Read file bytes
        contents = await file.read()
        
        # Run OCR in thread pool (non-blocking)
        loop = asyncio.get_event_loop()
        text = await loop.run_in_executor(_ocr_executor, _process_image_ocr, contents)

        return PlainTextResponse(content=text)

    except UnidentifiedImageError:
        raise HTTPException(status_code=400, detail="Invalid image format")
    except pytesseract.TesseractNotFoundError:
        raise HTTPException(
            status_code=503, 
            detail="Tesseract OCR not found. Install with: sudo apt-get install tesseract-ocr"
        )
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=f"Tesseract failed: {str(e)}")
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