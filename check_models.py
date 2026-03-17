import os
import google.generativeai as genai
from dotenv import load_dotenv

def list_models():
    # Load environment variables
    load_dotenv()
    api_key = os.getenv('GEMINI_API_KEY')
    
    if not api_key:
        print("Error: GEMINI_API_KEY not found in .env file.")
        return

    # Configure the library
    genai.configure(api_key=api_key)

    print("Checking available models for your API Key...\n")
    
    try:
        models = genai.list_models()
        
        print(f"{'Model Name':<40} | {'Supported Methods'}")
        print("-" * 80)
        
        image_gen_models = []
        
        for m in models:
            methods = ", ".join(m.supported_generation_methods)
            print(f"{m.name:<40} | {methods}")
            
            # Check for image generation strings (usually 'generateContent' is for text/multimodal)
            # Image generation models in Google AI Studio often have 'imagen' or specific flags
            if 'image' in m.name.lower() or 'image' in m.description.lower():
                image_gen_models.append(m)

        if image_gen_models:
            print("\nPotential Image Generation Models Found:")
            for m in image_gen_models:
                print(f"- {m.name}: {m.description}")
        else:
            print("\nNo explicit 'Text-to-Image' models found in the standard list.")
            print("Note: Gemini models (like gemini-1.5-flash) are Multimodal (Text/Image/Video input) but usually output Text/Code.")
            print("For dedicated Image Generation (Imagen 3), check Vertex AI or specific AI Studio beta features.")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    list_models()
