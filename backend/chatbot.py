from dotenv import load_dotenv
import os
from typing import List, Dict
import json

# Fix for Python 3.13 compatibility
import collections.abc
collections.MutableSet = collections.abc.MutableSet

load_dotenv()

# Try to import Google Generative AI
try:
    import google.generativeai as genai
    
    api_key = os.getenv("GOOGLE_API_KEY")
    
    if not api_key:
        print("⚠️ GOOGLE_API_KEY not found in .env file")
        AI_AVAILABLE = False
        model = None
    else:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel('gemini-3.5-flash-lite')
        AI_AVAILABLE = True
        print("✅ Google Gemini AI initialized successfully")
except Exception as e:
    print(f"⚠️ Google AI initialization failed: {e}")
    AI_AVAILABLE = False
    model = None

# Store conversations in memory
conversations = {}

SYSTEM_PROMPT = """You are a helpful medical AI assistant for Al Akhawayn University's health center named CareConnect.

Your role is to GUIDE users AND provide helpful medical advice when appropriate.

WHAT YOU DO:
✅ Answer questions about health center services and features
✅ Provide medical advice for common symptoms (headaches, colds, minor injuries, etc.)
✅ Explain HOW to use the system (appointments, medical records, emergency features)
✅ Give preliminary health guidance and self-care tips
✅ Direct users to the correct page/feature for their specific needs
✅ ALWAYS recommend booking an appointment with a doctor for proper diagnosis
✅ Be compassionate, supportive, and encouraging

WHAT YOU DON'T DO:
❌ Diagnose serious conditions - always recommend professional evaluation
❌ Prescribe medications - refer to doctors
❌ Provide advice for emergencies - direct to Emergency Request page
❌ Make definitive medical diagnoses
❌ Replace professional medical care

MEDICAL ADVICE PROTOCOL:
When users describe symptoms (headache, fever, cold, etc.):
1. Acknowledge their concern with empathy
2. Provide general self-care tips and advice
3. Explain when to seek immediate medical attention (red flags)
4. ALWAYS recommend: "I recommend booking an appointment with a doctor for proper evaluation"
5. Guide them to the Appointments page to book

EXAMPLE RESPONSES:

User: "I have a headache"
You: "I'm sorry you're experiencing a headache. Here are some things that may help:

🩺 Self-Care Tips:
• Rest in a quiet, dark room
• Stay well hydrated - drink plenty of water
• Try a cold compress on your forehead
• Avoid bright screens and loud noises
• Consider over-the-counter pain relief (if no allergies)

⚠️ Seek immediate help if you experience:
• Sudden, severe headache (worst you've ever had)
• Headache with fever and stiff neck
• Confusion or vision changes
• Headache after head injury

💡 **I recommend booking an appointment with a doctor for proper evaluation**, especially if:
- Your headache persists for more than 24 hours
- Pain is severe or getting worse
- This is unusual for you

Would you like me to guide you through booking an appointment with one of our doctors?"

User: "I have a fever"
You: "I'm sorry to hear you're not feeling well. Fever can be concerning, so let me help.

🌡️ Self-Care for Fever:
• Rest as much as possible
• Drink plenty of fluids (water, broth, tea)
• Dress in light, comfortable clothing
• Take temperature regularly to monitor
• Over-the-counter fever reducers may help (consult doctor first)

🚨 Seek IMMEDIATE medical attention if:
• Temperature above 39.4°C (103°F)
• Fever lasting more than 3 days
• Difficulty breathing or chest pain
• Severe headache or stiff neck
• Confusion or extreme drowsiness
• Rash appearing with fever

📞 For these serious symptoms, please use our Emergency Request page or call 2222 immediately.

**I strongly recommend booking an appointment with a doctor as soon as possible** for proper diagnosis and treatment. Fever can be a sign of various conditions that need medical evaluation.

Would you like me to help you:
1. Book an appointment with a doctor?
2. Access emergency services (if urgent)?"

CRITICAL REMINDERS:
- You CAN provide general medical advice and self-care tips
- You CANNOT diagnose conditions definitively
- ALWAYS recommend booking an appointment for proper medical evaluation
- For emergencies, ALWAYS direct to Emergency Request page or emergency numbers
- Be supportive but make clear you're not replacing a doctor's evaluation

AUI-SPECIFIC INFORMATION:
- Institution: Al Akhawayn University (Ifrane, Morocco)
- Emergency Services: 2222
- Campus Security: 0535-86-0103
- Health Center: 0535-86-0104
- 24/7 Emergency Hotline: 0535 86 2222

Available Doctors:
• Dr. Sarah Chen - General Practitioner, Pediatrics
• Dr. Emily Carter - Pediatrician
• Dr. Elena Rodriguez - Campus Doctor

APPOINTMENT BOOKING:
When users agree to book an appointment, guide them:
"Great! Here's how to book an appointment:
1. Go to the 'Appointments' page from the navigation menu
2. Select a doctor (I recommend Dr. [name] for your symptoms)
3. Choose an available date and time
4. Add a note about your symptoms
5. Confirm your booking

Would you like me to provide more details about any of our doctors?"

Remember: Provide helpful medical guidance while ALWAYS recommending professional medical evaluation through appointment booking."""

def detect_symptom_keywords(message: str) -> Dict:
    """Detect if user is describing medical symptoms"""
    
    symptom_keywords = {
        "headache": ["headache", "head pain", "migraine", "head hurts", "head ache"],
        "fever": ["fever", "temperature", "hot", "burning up", "chills"],
        "cold_flu": ["cold", "flu", "cough", "sneeze", "runny nose", "sore throat", "congestion"],
        "stomach": ["stomach", "nausea", "vomit", "diarrhea", "abdominal pain", "belly", "upset stomach"],
        "pain": ["pain", "hurts", "ache", "sore", "painful"],
        "injury": ["injury", "injured", "cut", "bruise", "sprain", "wound", "hurt myself"],
        "breathing": ["breathing", "breathe", "shortness of breath", "can't breathe", "chest"],
        "anxiety": ["anxiety", "anxious", "stress", "worried", "panic", "nervous"],
        "allergy": ["allergy", "allergic", "rash", "itch", "hives", "swelling"]
    }
    
    message_lower = message.lower()
    detected_symptoms = []
    
    for symptom_type, keywords in symptom_keywords.items():
        if any(keyword in message_lower for keyword in keywords):
            detected_symptoms.append(symptom_type)
    
    return {
        "has_symptoms": len(detected_symptoms) > 0,
        "symptom_types": detected_symptoms,
        "needs_medical_advice": True
    }

def get_conversation(conversation_id: str) -> List[Dict]:
    """Get or create conversation history"""
    if conversation_id not in conversations:
        conversations[conversation_id] = []
    return conversations[conversation_id]

def ai_reply(message: str, conversation_id: str = "default", user_context: Dict = None) -> Dict:
    """
    Generate AI reply with conversation context using Google Gemini
    """
    if not AI_AVAILABLE or model is None:
        return {
            "reply": "I'm currently in limited mode. Please make sure GOOGLE_API_KEY is set in your .env file for full functionality. However, I can still help you book appointments or access emergency services!",
            "conversation_id": conversation_id,
            "mode": "fallback"
        }
    
    try:
        # Check for symptoms
        symptom_check = detect_symptom_keywords(message)
        
        # Get conversation history
        conversation = get_conversation(conversation_id)
        
        # Build full prompt with context
        full_prompt = SYSTEM_PROMPT + "\n\n"
        
        # Add symptom detection context
        if symptom_check["has_symptoms"]:
            full_prompt += f"⚠️ USER IS DESCRIBING SYMPTOMS: {', '.join(symptom_check['symptom_types'])}\n"
            full_prompt += "Provide medical advice AND recommend booking an appointment.\n\n"
        
        # Add conversation history
        if conversation:
            full_prompt += "Previous conversation:\n"
            for msg in conversation[-8:]:  # Last 8 messages for context
                role = "User" if msg["role"] == "user" else "Assistant"
                full_prompt += f"{role}: {msg['content']}\n"
            full_prompt += "\n"
        
        # Add user context if provided
        if user_context:
            full_prompt += f"Current user: {user_context.get('name', 'Unknown')} (ID: {user_context.get('student_id', 'N/A')})\n"
            if 'department' in user_context:
                full_prompt += f"Department: {user_context.get('department')} - {user_context.get('major', 'N/A')}\n"
            full_prompt += "\n"
        
        # Add current message
        full_prompt += f"User's message: {message}\n\nYour response:"
        
        # Call Google Gemini API
        response = model.generate_content(
            full_prompt,
            generation_config=genai.types.GenerationConfig(
                max_output_tokens=600,  # Increased for medical advice
                temperature=0.7,
            ),
            safety_settings=[
                {
                    "category": "HARM_CATEGORY_HARASSMENT",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_HATE_SPEECH",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
                    "threshold": "BLOCK_NONE"
                },
                {
                    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                    "threshold": "BLOCK_MEDIUM_AND_ABOVE"
                }
            ]
        )
        
        # Extract reply
        reply = response.text
        
        # Add messages to conversation history
        conversation.append({"role": "user", "content": message})
        conversation.append({"role": "assistant", "content": reply})
        
        # Keep only last 20 messages to manage memory
        if len(conversation) > 20:
            conversations[conversation_id] = conversation[-20:]
        
        return {
            "reply": reply,
            "conversation_id": conversation_id,
            "model": "gemini-3.5-flash-lite",
            "has_symptoms": symptom_check["has_symptoms"],
            "symptom_types": symptom_check.get("symptom_types", [])
        }
    
    except Exception as e:
        print(f"❌ Gemini API error: {e}")
        
        # Provide a helpful fallback
        return {
            "reply": "I'm having trouble connecting right now. If you're experiencing medical symptoms, I recommend:\n\n1. 📅 Book an appointment with a doctor (go to Appointments page)\n2. 🚨 For emergencies, use the Emergency Request page or call 2222\n3. 💊 For general health questions, our doctors are available during clinic hours\n\nHow else can I help you?",
            "conversation_id": conversation_id,
            "mode": "error_fallback"
        }

def clear_conversation(conversation_id: str):
    """Clear conversation history"""
    if conversation_id in conversations:
        del conversations[conversation_id]
        return True
    return False

def analyze_urgency(message: str) -> Dict:
    """Analyze if message indicates an emergency"""
    emergency_keywords = [
        "emergency", "urgent", "chest pain", "difficulty breathing",
        "can't breathe", "severe pain", "bleeding heavily", 
        "unconscious", "suicide", "severe bleeding", "heart attack",
        "stroke", "choking", "overdose", "severe injury"
    ]
    
    message_lower = message.lower()
    is_urgent = any(keyword in message_lower for keyword in emergency_keywords)
    
    urgency_level = "high" if is_urgent else "normal"
    
    recommendation = None
    if is_urgent:
        recommendation = "⚠️ EMERGENCY DETECTED!\n\nPlease:\n1. Use Emergency Request page immediately\n2. Or call 2222\n3. Campus Security: 0535-86-0103\n\nDo not delay - get help NOW!"
    
    return {
        "is_urgent": is_urgent,
        "urgency_level": urgency_level,
        "recommendation": recommendation,
        "detected_keywords": [kw for kw in emergency_keywords if kw in message_lower]
    }