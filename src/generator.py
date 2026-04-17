# src/generator.py
from google import genai

def generate_tests(code_content, api_key):
    print("🧠 AI eka tests hithanawa...")
    client = genai.Client(api_key=api_key)
    
    # Prompt eka thawa strict karamu
    prompt = f"""
    You are an expert Python tester. Generate pytest unit tests for the following Python code. 
    IMPORTANT RULES:
    1. Return ONLY valid Python code.
    2. Do NOT include markdown code blocks (like ```python).
    3. Do NOT include any explanations.
    4. Make sure imports are on separate lines.
    
    Code to test:
    {code_content}
    """
    
    response = client.models.generate_content(
        model='gemini-2.5-flash', 
        contents=prompt
    )
    
    test_code = response.text
    
    # AI eka weradila hari markdown (```python) dammoth ewa ain karamu (Cleaning)
    test_code = test_code.replace("```python", "")
    test_code = test_code.replace("```", "")
    test_code = test_code.strip() # Anawashya his then ain karanawa
    
    return test_code