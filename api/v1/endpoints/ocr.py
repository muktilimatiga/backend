from fastapi import APIRouter, File, UploadFile, HTTPException
from fastapi.responses import PlainTextResponse
from PIL import Image, ImageOps, ImageFilter, UnidentifiedImageError
import pytesseract
import io

router = APIRouter()

# Supported text file extensions for direct reading
TEXT_FILE_EXTENSIONS = {'.txt', '.py', '.js', '.ts', '.jsx', '.tsx', '.md', '.json', 
                        '.yaml', '.yml', '.xml', '.html', '.css', '.sh', '.bash',
                        '.java', '.c', '.cpp', '.h', '.go', '.rs', '.rb', '.php'}

@router.post("/ocr")
def extract_text(file: UploadFile = File(...)):
    """
    Upload an image and extract text using OCR.
    Note: 'async def' removed to run this in a threadpool (OCR is blocking).
    """
    if file.content_type not in ["image/jpeg", "image/png", "image/webp"]:
        raise HTTPException(status_code=400, detail="Invalid file type")
        
    try:
        # 1. Read file
        contents = file.file.read()
        image = Image.open(io.BytesIO(contents))

        # --- PREPROCESSING START ---
        # Convert RGBA to RGB (remove alpha channel) - required for ImageOps.invert
        if image.mode == 'RGBA':
            image = image.convert('RGB')
        
        # Invert for dark mode screenshots
        image = ImageOps.invert(image)
        
        # Convert to Grayscale (removes colored syntax highlighting which confuses OCR)
        image = image.convert('L')
        
        # Upscale 3x for better character recognition (increased from 2x)
        image = image.resize((image.width * 3, image.height * 3), Image.Resampling.LANCZOS)
        
        # Sharpen the image to make text edges clearer
        image = image.filter(ImageFilter.SHARPEN)

        # Binarize: Force everything to pure black and white
        # 180 is the threshold; adjust 150-200 depending on background darkness.
        image = image.point(lambda x: 0 if x < 180 else 255, '1')
        # --- PREPROCESSING END ---

        # 2. Extract Text
        # --psm 6: Assumes a single uniform block of text (good for code)
        # --oem 3: LSTM neural network engine
        custom_config = r'--oem 3 --psm 6'
        
        text = pytesseract.image_to_string(image, config=custom_config, timeout=10)

        return PlainTextResponse(content=text.strip())

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