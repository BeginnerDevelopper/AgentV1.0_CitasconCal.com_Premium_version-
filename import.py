import os
import json
import logging
import requests
import tempfile
import uuid
import pytz
import re  # 🆕 PARA EXTRAER HORA
from dateutil import parser
from openai import OpenAI
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
from io import BytesIO

# ========================================
# 🔧 CONFIGURACIÓN INICIAL (IGUAL)
# ========================================
print("🔄 Cargando variables de entorno...")
if os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ[key.strip()] = value.strip().strip("'\"")
                print(f"✅ Cargada: {key.strip()}")
else:
    print("⚠️ No se encontró archivo .env")

print("=" * 80)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("whatsapp_voice_agent.log")],
)
logger = logging.getLogger(__name__)

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
CAL_API_KEY = os.getenv("CAL_API_KEY")
WHATSAPP_PHONE = os.getenv("WHATSAPP_PHONE")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
CAL_EVENT_TYPE_ID = int(os.getenv("CAL_EVENT_TYPE_ID", 3953936))
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

client = OpenAI(api_key=OPENAI_API_KEY)
DEFAULT_TIMEZONE = "America/New_York"
logger.info(f"⏰ Zona horaria configurada: {DEFAULT_TIMEZONE}")

GOOGLE_SHEETS_AVAILABLE = False
try:
    import gspread
    from google.oauth2.service_account import Credentials
    GOOGLE_SHEETS_AVAILABLE = True
except ImportError:
    print("⚠️ Google Sheets no disponible. Instala con: pip install gspread google-auth-oauthlib")
    print("💡 El código funcionará sin Google Sheets")

print("\n🔍 DEBUG - Variables de entorno cargadas:")
print(f"  TWILIO_ACCOUNT_SID: {'✅' if TWILIO_ACCOUNT_SID else '❌'}")
print(f"  TWILIO_AUTH_TOKEN: {'✅' if TWILIO_AUTH_TOKEN else '❌'}")
print(f"  WHATSAPP_PHONE: {'✅' if WHATSAPP_PHONE else '❌'}")
print(f"  TWILIO_PHONE_NUMBER: {'✅' if TWILIO_PHONE_NUMBER else '❌'}")
print(f"  CAL_API_KEY: {'✅' if CAL_API_KEY else '❌'}")
print(f"  OPENAI_API_KEY: {'✅' if OPENAI_API_KEY else '❌'}")
print(f"  CAL_EVENT_TYPE_ID: ✅ {CAL_EVENT_TYPE_ID}")
print(f"  GOOGLE_SHEETS: {'✅' if GOOGLE_SHEETS_AVAILABLE else '⚠️  Opcional'}")


class GoogleSheetsIntegration:
    """Maneja la integración con Google Sheets para persistencia de datos"""

    def __init__(self):
        self.gc = None
        self.sheet = None
        self.sheet_id = os.getenv("GOOGLE_SHEETS_ID")
        self.credentials_path = os.getenv("GOOGLE_SHEETS_CREDENTIALS")

        if not GOOGLE_SHEETS_AVAILABLE:
            logger.warning("⚠️ Google Sheets deshabilitado (faltan paquetes)")
            return

        if self.sheet_id and self.credentials_path:
            try:
                scope = [
                    "https://www.googleapis.com/auth/spreadsheets   ",
                    "https://www.googleapis.com/auth/drive.file   ",
                ]
                creds = Credentials.from_service_account_file(
                    self.credentials_path, scopes=scope
                )
                self.gc = gspread.authorize(creds)
                spreadsheet = self.gc.open_by_key(self.sheet_id)
                self.sheet = spreadsheet.sheet1
                self._ensure_headers()
                logger.info("✅ Google Sheets integrado correctamente")
            except Exception as e:
                logger.error(f"❌ Error inicializando Google Sheets: {e}")
                self.gc = None
                self.sheet = None
        else:
            logger.info("💾 Google Sheets: No configurado (opcional)")

    def _ensure_headers(self):
        """Asegura que la hoja tenga los headers necesarios"""
        try:
            if not self.sheet:
                return False
            first_row = self.sheet.row_values(1)
            if not first_row:
                headers = [
                    "Contact_date",
                    "Phone",
                    "Name",
                    "Email",
                    "Booked_date",
                    "Status",
                    "Language",
                    "Notes",
                ]
                self.sheet.update("A1:H1", [headers])
                self.sheet.format(
                    "A1:H1",
                    {
                        "backgroundColor": {"red": 0.8, "green": 0.8, "blue": 0.8},
                        "textFormat": {"bold": True},
                    },
                )
                logger.info("✅ Headers creados en Google Sheets")
            return True
        except Exception as e:
            logger.error(f"❌ Error creando headers: {e}")
            return False

    def save_booking_data(
        self,
        phone_number,
        nombre,
        email,
        fecha_cita,
        idioma,
        estado="Completado",
        notas="",
    ):
        """Guarda los datos de una cita en Google Sheets"""
        if not self.sheet:
            logger.warning("⚠️ Google Sheets no disponible para guardar datos")
            return False

        try:
            row_data = [
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                phone_number,
                nombre,
                email,
                fecha_cita,
                estado,
                idioma,
                notas,
            ]
            num_rows = len(self.sheet.get_all_values())
            self.sheet.update(f"A{num_rows + 1}:H{num_rows + 1}", [row_data])
            logger.info(f"✅ Datos guardados: {nombre} ({phone_number})")
            return True
        except Exception as e:
            logger.error(f"❌ Error guardando en Google Sheets: {e}")
            return False


# ========================================
# 💬 RESPUESTAS MULTILINGÜES COMPLETAS
# ========================================
class LanguageResponses:
    def __init__(self):
        self.language_responses = {
            "es": {
                "greeting": "¡Hola! 👋 Soy tu asistente de voz inteligente. ¿En qué puedo ayudarte hoy?",
                "booking_title": "📅 **Reserva de Cita**",
                "booking_success": "✅ ¡Cita reservada con éxito!",
                "booking_error": "❌ Error reservando cita.",
                "appointment_scheduled": "✅ ¡Tu cita ha sido programada exitosamente! Te llegará un email de confirmación.📍 **Enlace de la reunión:** {meeting_url}",
                "help": "Puedo ayudarte con: agendar citas, responder preguntas, o proporcionar información sobre nuestros servicios. ¿Qué necesitas?",
                "data_extraction_request": "📋 Para agendar tu cita necesito la siguiente información:\n\n• *👤 Nombre completo*\n• *📩 Correo electrónico*\n• *🕓 ¿Para cuándo quieres la cita?* (ej: mañana, lunes, 25 de noviembre)\n\n⚡ *Comencemos — ¿cuál es tu nombre completo?*",
                "ask_for_name": "Por favor, ¿podrías proporcionarme tu nombre completo?",
                "ask_for_email": "Perfecto, {name}. Ahora necesito tu correo electrónico para completar la reserva.",
                "ask_for_date": "Excelente, {name}. ¿Para cuándo te gustaría agendar tu cita? (ej: mañana, lunes, fecha específica)",
                "name_received": "Gracias, {name}. ¿Podrías proporcionarme tu correo electrónico?",
                "email_received": "Perfecto, {name}. ¿Para cuándo quieres tu cita?",
                "data_extracted_success": "✅ ¡Perfecto! He extraído la siguiente información:\n\n• **Nombre:** {name}\n• **Email:** {email}\n• **Fecha:** {date}\n\nAhora procederé a agendar tu cita en zona horaria de Nueva York...",
                "language_change_spanish": "¡Por supuesto! Con mucho gusto continuaré conversando contigo en español. ¿En qué puedo ayudarte?",
                "language_change_comfortable": "No te preocupes, hablaremos como te sientas más cómodo(a). ¿Prefieres que sigamos en español?",
                "name_provided_partial": "Entendido, {name}. ¿Tienes correo electrónico que pueda usar para la cita?",
                "email_provided_partial": "Perfecto, {name}. Ya tengo tu email: {email}. ¿Para cuándo quieres tu cita?",
                "booking_initiated": "🚀 ¡Excelente! Te ayudo a agendar tu cita.\n\n{user_name}{user_email}{user_date}\n\n**Datos que necesito completar:**\n{remaining_fields}",
                "trial_mode_warning": "⚠️ **Modo Trial de Twilio**: Solo puedo enviar mensajes a números verificados. Asegúrate de que tu número esté verificado en la consola de Twilio.",
                "generic_response": "🤔 Lo siento, no entendí tu mensaje. ¿Podrías repetirlo de otra forma?",
                "past_date_error": "⚠️ La fecha/hora que elegiste ya pasó. Por favor elige una fecha/hora futura.",
                "slot_conflict_retry": "⚠️ El horario {original_time} ya fue tomado. Intentando con el siguiente disponible: {new_time}",
                "all_slots_full": "❌ Lamentablemente no hay slots disponibles en los próximos días. Por favor contacta manualmente.",
                "availability_error": "⚠️ No hay disponibilidad para esa fecha. Por favor elige otro día/hora.",
                "insufficient_notice_error": "⚠️ Necesitas agendar con al menos {minimum_hours} horas de anticipación. El horario {requested_time} no está disponible. Prueba con: {suggested_time} (es decir, {pretty_time})",
                "time_out_of_bounds_error": "⚠️ El horario {requested_time} está fuera del horario laboral o ventana de reserva. Intentando con: {next_available}",
            },
            "en": {
                "greeting": "Hello! 👋 I'm your intelligent voice assistant. How can I help you today?",
                "booking_title": "📅 **Appointment Booking**",
                "booking_success": "✅ Appointment booked successfully!",
                "booking_error": "❌ Error booking appointment.",
                "appointment_scheduled": "✅ Your appointment has been successfully scheduled! You will receive a confirmation email.\n📍 **Meeting link:*{meeting_url}",
                "help": "I can help you with: booking appointments, answering questions, or providing information about our services. What do you need?",
                "data_extraction_request": "📋 To schedule your appointment I need the following information:\n\n• *👤 Full name*\n• *📩 Email address*\n• *🕓 When do you want the appointment?* (e.g.: tomorrow, Monday, November 25)\n\n⚡ *Let's start — What is your full name?*",
                "ask_for_name": "Please, could you provide me with your full name?",
                "ask_for_email": "Perfect, {name}. Now I need your email address to complete the booking.",
                "ask_for_date": "Excellent, {name}. When would you like to schedule your appointment? (e.g.: tomorrow, Monday, specific date)",
                "name_received": "Thank you, {name}. Could you provide me with your email address?",
                "email_received": "Perfect, {name}. When do you want your appointment?",
                "data_extracted_success": "✅ Perfect! I have extracted the following information:\n\n• **Name:** {name}\n• **Email:** {email}\n• **Date:** {date}\n\nNow I will proceed to schedule your appointment in New York timezone...",
                "language_change_spanish": "Of course! I'm pleased to continue conversing with you in Spanish. How can I help you?",
                "language_change_comfortable": "Don't worry, we'll speak however you feel most comfortable. Would you like to continue in Spanish?",
                "name_provided_partial": "Got it, {name}. Do you have an email address I can use for the appointment?",
                "email_provided_partial": "Perfect, {name}. I already have your email: {email}. When do you want your appointment?",
                "booking_initiated": "🚀 Great! I'll help you schedule your appointment.\n\n{user_name}{user_email}{user_date}\n\n**Data I need to complete:**\n{remaining_fields}",
                "trial_mode_warning": "⚠️ **Twilio Trial Mode**: I can only send messages to verified numbers. Make sure your number is verified in the Twilio console.",
                "generic_response": "🤔 I'm sorry, I didn't understand your message. Could you please rephrase it?",
                "past_date_error": "⚠️ The date/time you chose has already passed. Please select a future date/time.",
                "slot_conflict_retry": "⚠️ The time slot {original_time} has already been taken. Trying the next available one: {new_time}",
                "all_slots_full": "❌ Unfortunately, there are no available slots in the next few days. Please reach out manually.",
                "availability_error": "⚠️ There’s no availability for that date. Please pick another day or time.",
                "insufficient_notice_error": "⚠️ You need to book at least {minimum_hours} hours in advance. The time {requested_time} isn’t available. Try this instead: {suggested_time} ({pretty_time})",
                "time_out_of_bounds_error": "⚠️ The time {requested_time} is outside the booking window. Trying: {next_available}",

            },
            "fr": {
                "greeting": "Bonjour! 👋 Je suis votre assistant vocal intelligent. Comment puis-je vous aider aujourd'hui?",
                "booking_title": "📅 **Réservation de Rendez-vous**",
                "booking_success": "✅ Rendez-vous réservé avec succès!",
                "booking_error": "❌ Erreur lors de la réservation.",
                "appointment_scheduled": "✅ Votre rendez-vous a été programmé avec succès! Vous recevrez un email de confirmation.\n 📍 **Lien de la réunion:** {meeting_url}",
                "help": "Je peux vous aider avec: réserver des rendez-vous, répondre aux questions, ou fournir des informations sur nos services. De quoi avez-vous besoin?",
                "data_extraction_request": "📋 Pour planifier votre rendez-vous j'ai besoin des informations suivantes:\n\n• *👤 Nom complet*\n• *📩 Adresse e-mail*\n• *🕓 Quand voulez-vous le rendez-vous?* (ex: demain, lundi, 25 novembre)\n\n⚡ *Commençons — Quel est votre nom complet?*",
                "ask_for_name": "S'il vous plaît, pourriez-vous me donner votre nom complet?",
                "ask_for_email": "Parfait, {name}. Maintenant j'ai besoin de votre adresse e-mail pour compléter la réservation.",
                "ask_for_date": "Excellent, {name}. Quand souhaitez-vous planifier votre rendez-vous? (ex: demain, lundi, date spécifique)",
                "name_received": "Merci, {name}. Pourriez-vous me donner votre adresse e-mail?",
                "email_received": "Parfait, {name}. Quand voulez-vous votre rendez-vous?",
                "data_extracted_success": "✅ Parfait! J'ai extrait les informations suivantes:\n\n• **Nom:** {name}\n• **E-mail:** {email}\n• **Date:** {date}\n\nMaintenant je procéderai à planifier votre rendez-vous dans le fuseau horaire de New York...",
                "language_change_spanish": "Bien sûr! Avec plaisir, je continuerai à converser avec vous en espagnol. Comment puis-je vous aider?",
                "language_change_comfortable": "Ne vous inquiétez pas, nous parlerons comme vous vous sentez à l'aise. Préférez-vous continuer en espagnol?",
                "name_provided_partial": "Compris, {name}. Avez-vous une adresse e-mail que je puisse utiliser pour le rendez-vous?",
                "email_provided_partial": "Parfait, {name}. J'ai déjà votre e-mail: {email}. Quand voulez-vous votre rendez-vous?",
                "booking_initiated": "🚀 Excellent! Je vous aiderai à planifier votre rendez-vous.\n\n{user_name}{user_email}{user_date}\n\n**Données que je dois compléter:**\n{remaining_fields}",
                "trial_mode_warning": "⚠️ **Mode Trial Twilio**: Je ne peux envoyer des messages qu'aux numéros vérifiés. Assurez-vous que votre numéro est vérifié dans la console Twilio.",
                "generic_response": "🤔 Je suis désolé, je n'ai pas compris votre message. Pourriez-vous le reformuler?",
                "past_date_error": "⚠️ La date/heure que vous avez choisie est déjà passée. Veuillez sélectionner une date/heure future.",
                "slot_conflict_retry": "⚠️ Le créneau horaire {original_time} est déjà pris. Tentative avec le prochain disponible : {new_time}",
                "all_slots_full": "❌ Malheureusement, aucun créneau n’est disponible ces prochains jours. Veuillez contacter manuellement.",
                "availability_error": "⚠️ Aucune disponibilité pour cette date. Veuillez choisir un autre jour/heure.",
                "insufficient_notice_error": "⚠️ Vous devez réserver au moins {minimum_hours} heures à l'avance. Le créneau {requested_time} n’est pas disponible. Essayez plutôt : {suggested_time} ({pretty_time})",
                "time_out_of_bounds_error": "⚠️ Le créneau {requested_time} est en dehors de la période autorisée pour les réservations. Proposition : {next_available}",
                
            },
            "de": {
                "greeting": "Hallo! 👋 Ich bin Ihr intelligenter Sprachassistent. Wie kann ich Ihnen heute helfen?",
                "booking_title": "📅 **Terminbuchung**",
                "booking_success": "✅ Termin erfolgreich gebucht!",
                "booking_error": "❌ Fehler bei der Terminbuchung.",
                "appointment_scheduled": "✅ Ihr Termin wurde erfolgreich geplant! Sie erhalten eine Bestätigungs-E-Mail. \n📍 **Meeting-Link:** {meeting_url}",
                "help": "Ich kann Ihnen helfen mit: Terminbuchungen, Fragen beantworten, oder Informationen über unsere Dienstleistungen. Was brauchen Sie?",
                "data_extraction_request": "📋 Um Ihren Termin zu planen, benötige ich folgende Informationen:\n\n• *👤 Vollständiger Name*\n• *📩 E-Mail-Adresse*\n• *🕓 Wann möchten Sie den Termin?* (z.B.: morgen, Montag, 25. November)\n\n⚡ *Beginnen wir — Was ist Ihr vollständiger Name?*",
                "ask_for_name": "Bitte, könnten Sie mir Ihren vollständigen Namen geben?",
                "ask_for_email": "Perfekt, {name}. Jetzt brauche ich Ihre E-Mail-Adresse, um die Buchung abzuschließen.",
                "ask_for_date": "Ausgezeichnet, {name}. Wann möchten Sie Ihren Termin planen? (z.B.: morgen, Montag, bestimmtes Datum)",
                "name_received": "Danke, {name}. Könnten Sie mir Ihre E-Mail-Adresse geben?",
                "email_received": "Perfekt, {name}. Wann möchten Sie Ihren Termin?",
                "data_extracted_success": "✅ Perfekt! Ich habe folgende Informationen extrahiert:\n\n• **Name:** {name}\n• **E-Mail:** {email}\n• **Datum:** {date}\n\nJetzt werde ich Ihren Termin in der Zeitzone New York planen...",
                "language_change_spanish": "Natürlich! Mit Vergnügen werde ich weiterhin auf Spanisch mit Ihnen sprechen. Wie kann ich Ihnen helfen?",
                "language_change_comfortable": "Machen Sie sich keine Sorgen, wir werden so sprechen, wie es Ihnen am bequemsten ist. Möchten Sie auf Spanisch fortfahren?",
                "name_provided_partial": "Verstanden, {name}. Haben Sie eine E-Mail-Adresse, die ich für den Termin verwenden kann?",
                "email_provided_partial": "Perfekt, {name}. Ich habe bereits Ihre E-Mail: {email}. Wann möchten Sie Ihren Termin?",
                "booking_initiated": "🚀 Hervorragend! Ich helfe Ihnen dabei, Ihren Termin zu planen.\n\n{user_name}{user_email}{user_date}\n\n**Daten, die ich vervollständigen muss:**\n{remaining_fields}",
                "trial_mode_warning": "⚠️ **Twilio-Trial-Modus**: Ich kann nur Nachrichten an verifizierte Nummern senden. Stellen Sie sicher, dass Ihre Nummer in der Twilio-Konsole verifiziert ist.",
                "generic_response": "🤔 Tut mir leid, ich habe Ihre Nachricht nicht verstanden. Könnten Sie sie bitte anders formulieren?",
                "past_date_error": "⚠️ Das von Ihnen gewählte Datum/die Uhrzeit ist bereits vergangen. Bitte wählen Sie ein zukünftiges Datum/eine zukünftige Uhrzeit.",
                "slot_conflict_retry": "⚠️ Der Zeitpunkt {original_time} ist bereits vergeben. Versuche mit dem nächsten verfügbaren: {new_time}",
                "all_slots_full": "❌ Leider sind in den nächsten Tagen keine Termine mehr verfügbar. Bitte kontaktieren Sie manuell.",
                "availability_error": "⚠️ Für dieses Datum gibt es keine Verfügbarkeit. Bitte wählen Sie einen anderen Tag oder eine andere Uhrzeit.",
                "insufficient_notice_error": "⚠️ Sie müssen mindestens {minimum_hours} Stunden im Voraus buchen. Der Termin {requested_time} ist nicht verfügbar. Versuchen Sie stattdessen: {suggested_time} ({pretty_time})",
                "time_out_of_bounds_error": "⚠️ Der Termin {requested_time} liegt außerhalb des zulässigen Buchungsfensters. Vorschlag: {next_available}",
            },
            "it": {
                "greeting": "Ciao! 👋 Sono il tuo assistente vocale intelligente. Come posso aiutarti oggi?",
                "booking_title": "📅 **Prenotazione Appuntamento**",
                "booking_success": "✅ Appuntamento prenotato con successo!",
                "booking_error": "❌ Errore durante la prenotazione.",
                "appointment_scheduled": "✅ Il tuo appuntamento è stato programmato con successo! Riceverai un'email di conferma. \n📍 **Link dell'incontro:** {meeting_url}",
                "help": "Posso aiutarti con: prenotare appuntamenti, rispondere a domande, o fornire informazioni sui nostri servizi. Di cosa hai bisogno?",
                "data_extraction_request": "📋 Per programmare il tuo appuntamento ho bisogno delle seguenti informazioni:\n\n• *👤 Nome completo*\n• *📩 Indirizzo email*\n• *🕓 Quando vuoi l'appuntamento?* (es: domani, lunedì, 25 novembre)\n\n⚡ *Iniziamo — Qual è il tuo nome completo?*",
                "ask_for_name": "Per favore, potresti fornirmi il tuo nome completo?",
                "ask_for_email": "Perfetto, {name}. Ora ho bisogno del tuo indirizzo email per completare la prenotazione.",
                "ask_for_date": "Eccellente, {name}. Quando vorresti programmare il tuo appuntamento? (es: domani, lunedì, data specifica)",
                "name_received": "Grazie, {name}. Potresti fornirmi il tuo indirizzo email?",
                "email_received": "Perfetto, {name}. Quando vuoi il tuo appuntamento?",
                "data_extracted_success": "✅ Perfetto! Ho estratto le seguenti informazioni:\n\n• **Nome:** {name}\n• **Email:** {email}\n• **Data:** {date}\n\nOra procederò a programmare il tuo appuntamento nel fuso orario di New York...",
                "language_change_spanish": "Certamente! Con piacere continuerò a conversare con te in spagnolo. Come posso aiutarti?",
                "language_change_comfortable": "Non preoccuparti, parleremo come ti senti più a tuo agio. Preferisci continuare in spagnolo?",
                "name_provided_partial": "Capito, {name}. Hai un indirizzo email che posso usare per l'appuntamento?",
                "email_provided_partial": "Perfetto, {name}. Ho già la tua email: {email}. Quando vuoi il tuo appuntamento?",
                "booking_initiated": "🚀 Eccellente! Ti aiuterò a programmare il tuo appuntamento.\n\n{user_name}{user_email}{user_date}\n\n**Dati che devo completare:**\n{remaining_fields}",
                "trial_mode_warning": "⚠️ **Modalità di prova Twilio**: Posso inviare messaggi solo a numeri verificati. Assicurati che il tuo numero sia verificato nella console Twilio.",
                "generic_response": "🤔 Mi dispiace, non ho capito il tuo messaggio. Potresti riformularlo?",
                "past_date_error": "⚠️ La data/ora che hai scelto è già passata. Seleziona una data/ora futura.",
                "slot_conflict_retry": "⚠️ L'orario {original_time} è già occupato. Sto provando con il prossimo disponibile: {new_time}",
                "all_slots_full": "❌ Purtroppo non ci sono appuntamenti disponibili nei prossimi giorni. Si prega di contattare manualmente.",
                "availability_error": "⚠️ Non ci sono disponibilità per questa data. Si prega di scegliere un altro giorno/orario.",
                "insufficient_notice_error": "⚠️ È necessario prenotare con almeno {minimum_hours} ore di anticipo. L’orario {requested_time} non è disponibile. Prova con: {suggested_time} ({pretty_time})",
                "time_out_of_bounds_error": "⚠️ L’orario {requested_time} è al di fuori della finestra di prenotazione. Sto provando con: {next_available}",
            },
            "pt": {
                "greeting": "Olá! 👋 Sou seu assistente de voz inteligente. Como posso ajudá-lo hoje?",
                "booking_title": "📅 **Agendamento de Consulta**",
                "booking_success": "✅ Consulta agendada com sucesso!",
                "booking_error": "❌ Erro ao agendar consulta.",
                "appointment_scheduled": "✅ Sua consulta foi marcada com sucesso! Você receberá um email de confirmação \n📍 **Link da reunião:** {meeting_url}.",
                "help": "Posso ajudá-lo com: agendar consultas, responder perguntas, ou fornecer informações sobre nossos serviços. Do que você precisa?",
                "data_extraction_request": "📋 Para agendar sua consulta preciso das seguintes informações:\n\n• *👤 Nome completo*\n• *📩 Endereço de email*\n• *🕓 Quando quer a consulta?* (ex: amanhã, segunda-feira, 25 de novembro)\n\n⚡ *Vamos começar — Qual é o seu nome completo?*",
                "ask_for_name": "Por favor, poderia me fornecer seu nome completo?",
                "ask_for_email": "Perfeito, {name}. Agora preciso do seu endereço de email para completar o agendamento.",
                "ask_for_date": "Excelente, {name}. Quando gostaria de agendar sua consulta? (ex: amanhã, segunda-feira, data específica)",
                "name_received": "Obrigado, {name}. Poderia me fornecer seu endereço de email?",
                "email_received": "Perfeito, {name}. Quando quer sua consulta?",
                "data_extracted_success": "✅ Perfeito! Extraí as seguintes informações:\n\n• **Nome:** {name}\n• **Email:** {email}\n• **Data:** {date}\n\nAgora vou proceder para agendar sua consulta no fuso horário de Nova York...",
                "language_change_spanish": "Claro! Com muito prazer continuarei conversando com você em espanhol. Como posso ajudá-lo?",
                "language_change_comfortable": "Não se preocupe, falaremos como você se sentir mais à vontade. Prefere continuar em espanhol?",
                "name_provided_partial": "Entendi, {name}. Tem um endereço de email que eu possa usar para a consulta?",
                "email_provided_partial": "Perfeito, {name}. Já tenho seu email: {email}. Quando quer sua consulta?",
                "booking_initiated": "🚀 Excelente! Te ajudo a agendar sua consulta.\n\n{user_name}{user_email}{user_date}\n\n**Dados que preciso completar:**\n{remaining_fields}",
                "trial_mode_warning": "⚠️ **Modo de teste Twilio**: Só posso enviar mensagens para números verificados. Certifique-se de que seu número esteja verificado no console Twilio.",
                "generic_response": "🤔 Desculpe, não entendi sua mensagem. Você poderia reformulá-la?",
                "past_date_error": "⚠️ A data/hora que você escolheu já passou. Selecione uma data/hora futura.",
                "slot_conflict_retry": "⚠️ O horário {original_time} já foi reservado. Tentando com o próximo disponível: {new_time}",
                "all_slots_full": "❌ Infelizmente, não há horários disponíveis nos próximos dias. Por favor, entre em contato manualmente.",
                "availability_error": "⚠️ Não há disponibilidade para essa data. Por favor, escolha outro dia/horário.",
                "insufficient_notice_error": "⚠️ Você precisa agendar com pelo menos {minimum_hours} horas de antecedência. O horário {requested_time} não está disponível. Tente este: {suggested_time} ({pretty_time})",
                "time_out_of_bounds_error": "⚠️ O horário {requested_time} está fora do período permitido para reservas. Tentando com: {next_available}",
            },
        }

    def get_response(self, key, language="en", **kwargs):
        """Obtiene una respuesta con interpolación de variables - CON MANEJO DE ERRORES"""
        try:
            responses = self.language_responses.get(language, self.language_responses["en"])

            # 🛡️ SI LA CLAVE NO EXISTE, USAR UN FALLBACK SEGURO
            if key not in responses:
                logger.warning(
                    f"⚠️ Clave '{key}' no encontrada en idioma '{language}', usando fallback"
                )
                fallback_key = "generic_response"
                if fallback_key in responses:
                    template = responses[fallback_key]
                else:
                    # Último recurso: mensaje fijo
                    return f"🤔 No entendí. ¿Podrías repetir? (Error: clave {key} no encontrada)"
            else:
                template = responses[key]

            return template.format(**kwargs)
        except Exception as e:
            logger.error(
                f"❌ Error obteniendo respuesta para clave '{key}' en idioma '{language}': {e}"
            )
            return "🤔 Lo siento, hubo un error. Por favor, intenta nuevamente."


# ========================================
# 🤖 AGENTE WHATSAPP CON VOZ
# ========================================
class ConversationState:
    def __init__(self, phone_number):
        self.phone_number = phone_number
        self.state = "initial"
        self.language = "en"
        self.data = {"name": None, "email": None, "date": None}
        self.last_updated = datetime.now()


class WhatsAppVoiceAgent:
    def __init__(self):
        self.language_responses_obj = LanguageResponses()
        self.language_responses = self.language_responses_obj.language_responses
        self.default_timezone = DEFAULT_TIMEZONE
        self.conversation_states = {}
        self.sheets_integration = GoogleSheetsIntegration()
        logger.info("🤖 Agente de voz WhatsApp inicializado")
        logger.info(f"⏰ Zona horaria configurada: {self.default_timezone}")

    def get_response(self, key, language="en", **kwargs):
        return self.language_responses_obj.get_response(key, language, **kwargs)

    def get_or_create_conversation_state(self, phone_number):
        if phone_number not in self.conversation_states:
            self.conversation_states[phone_number] = ConversationState(phone_number)
        return self.conversation_states[phone_number]

    def detect_language(self, text):
        """🌍 DETECCIÓN DE IDIOMA COMPLETA - 6 IDIOMAS"""
        try:
            if not text or not isinstance(text, str) or not text.strip():
                return "en"

            text_lower = text.lower().strip()

            # 🎯 INDICADORES DE IDIOMA (prioridad alta para booking)
            high_priority_english = [
                "hi",
                "hello",
                "hey",
                "greetings",
                "my name is",
                "i am",
                "i'm",
                "call me",
                "i would like",
                "i'd like",
                "i want",
                "appointment",
                "schedule",
                "book",
                "meeting",
                "demo",
                "consultation",
                "call back",
                "phone",
                "email",
                "time",
                "today",
                "tomorrow",
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "weekend",
                "morning",
                "afternoon",
                "evening",
                "thanks",
                "please",
                "how are you",
                "how do you do",
                "good morning",
                "good afternoon",
                "good evening",
                "want",
                "would",
                "like",
                "thank",
            ]

            # Spanish
            spanish_words = [
                "hola",
                "gracias",
                "por favor",
                "cómo",
                "qué",
                "dónde",
                "cuándo",
                "por qué",
                "amigo",
                "amiga",
                "bien",
                "muy",
                "hasta",
                "luego",
                "ahora",
                "mi nombre es",
                "me llamo",
                "quisiera",
                "quiero",
                "cita",
                "agendar",
                "reunión",
                "demo",
                "consulta",
                "llamada",
            ]

            # French
            french_words = [
                "bonjour",
                "merci",
                "s'il vous plaît",
                "comment",
                "quoi",
                "où",
                "quand",
                "pourquoi",
                "ami",
                "bien",
                "très",
                "à bientôt",
                "maintenant",
                "mon nom est",
                "je suis",
                "je voudrais",
                "rendez",
                "rdv",
                "consultation",
                "appel",
            ]

            # German
            german_words = [
                "hallo",
                "danke",
                "bitte",
                "wie",
                "was",
                "wo",
                "wann",
                "warum",
                "freund",
                "gut",
                "sehr",
                "bis bald",
                "jetzt",
                "mein name ist",
                "ich bin",
                "ich möchte",
                "termin",
                "buchen",
                "meeting",
                "beratung",
                "anruf",
            ]

            # Italian
            italian_words = [
                "ciao",
                "grazie",
                "per favore",
                "come",
                "cosa",
                "dove",
                "quando",
                "perché",
                "amico",
                "bene",
                "molto",
                "a presto",
                "ora",
                "mi chiamo",
                "sono",
                "vorrei",
                "appuntamento",
                "prenotare",
                "incontro",
                "consulta",
                "chiamata",
            ]

            # Portuguese
            portuguese_words = [
                "olá",
                "obrigado",
                "por favor",
                "como",
                "o que",
                "onde",
                "quando",
                "por que",
                "amigo",
                "bem",
                "muito",
                "até logo",
                "agora",
                "meu nome é",
                "eu sou",
                "eu gostaria",
                "encontro",
                "agendar",
                "consulta",
                "ligação",
            ]

            # 🛡️ Nombres comunes que NO deben afectar la detección
            common_names = [
                "jackson",
                "james",
                "john",
                "mike",
                "tom",
                "sam",
                "paul",
                "mark",
                "luke",
                "pete",
                "jamillet",
                "jamilet",
                "maria",
                "ana",
                "anna",
                "clara",
                "marta",
                "martin",
                "diego",
                "carlos",
                "luis",
                "jose",
                "francesco",
                "mario",
                "antonio",
                "roberto",
            ]

            # Verificar si el mensaje es solo nombres
            words = text_lower.split()
            if len(words) <= 4 and all(word in common_names for word in words):
                logger.info(
                    f"🌍 Detección: Solo nombres detectados, usando inglés por defecto"
                )
                return "en"

            # Prioridad: frases en inglés de booking
            for phrase in high_priority_english:
                if phrase in text_lower:
                    return "en"

            # Contar palabras por idioma (excluyendo nombres)
            filtered_words = [word for word in words if word not in common_names]
            filtered_text = " ".join(filtered_words)

            counts = {
                "es": sum(1 for word in spanish_words if word in filtered_text),
                "en": sum(1 for word in high_priority_english if word in filtered_text),
                "fr": sum(1 for word in french_words if word in filtered_text),
                "de": sum(1 for word in german_words if word in filtered_text),
                "it": sum(1 for word in italian_words if word in filtered_text),
                "pt": sum(1 for word in portuguese_words if word in filtered_text),
            }

            best_lang = max(counts, key=counts.get)
            score = counts[best_lang]

            if score == 0 or len(text.strip()) < 5:
                return "en"

            logger.info(f"🌍 Idioma detectado: {best_lang} (score: {score})")
            return best_lang

        except Exception as e:
            logger.error(f"❌ Error detectando idioma: {e}")
            return "en"

    def extract_booking_data(self, message, language="en"):
        """🎙️ EXTRACCIÓN DE DATOS CON GPT-4O-MINI - MULTILINGÜE"""
        try:
            if not OPENAI_API_KEY:
                logger.warning("⚠️ OpenAI API key no disponible, usando extracción básica")
                return self.basic_data_extraction(message, language)

            # Prompts específicos por idioma
            language_names = {
                "es": "español",
                "en": "inglés",
                "fr": "francés",
                "de": "alemán",
                "it": "italiano",
                "pt": "portugués",
            }

            system_prompt = f"""Eres un asistente especializado en extracción de datos para agendamiento de citas.

**INSTRUCCIONES:**
- Analiza el mensaje del usuario y extrae SOLO los datos que estén claramente proporcionados
- Responde SIEMPRE en {language_names.get(language, language)}
- Si un dato no está claro o presente, responde "Not specified"
- NO inventes información
- SI el usuario menciona una hora específica (ej: "12 PM", "3 PM", "14:00"), INCLÚYELA EN EL CAMPO "fecha"

**DATOS A EXTRAER:**
1. **nombre**: Nombre completo del usuario (primer y apellido)
2. **email**: Dirección de correo electrónico válida
3. **fecha**: Cuándo quiere la cita (ej: "tomorrow at 12 PM", "mañana a las 3 PM", "Monday 10 AM", "25 noviembre 2025 14:00")

**FORMATO DE RESPUESTA:**
Responda ÚNICAMENTE con un JSON válido sin texto adicional:
{{
    "nombre": "valor_extraído_o_No_especificado",
    "email": "valor_extraído_o_No_especificado", 
    "fecha": "valor_extraído_o_No_especificado"
}}"""

            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"Mensaje del usuario: {message}"},
                ],
                max_tokens=200,
                temperature=0.3,
            )

            response_text = response.choices[0].message.content.strip()
            logger.info(f"🔍 Extracción OpenAI: {response_text}")
            return json.loads(response_text)

        except json.JSONDecodeError:
            logger.warning(f"⚠️ No se pudo parsear JSON, usando extracción básica")
            return self.basic_data_extraction(message, language)
        except Exception as e:
            logger.error(f"❌ Error con extracción OpenAI: {e}")
            return self.basic_data_extraction(message, language)

    def basic_data_extraction(self, message, language="en"):
        """🔍 EXTRACCIÓN BÁSICA SIN OPENAI - MULTILINGÜE"""
        try:
            import re

            message_lower = message.lower()

            # Email (universal)
            email_pattern = r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"
            email_match = re.search(email_pattern, message)
            email = email_match.group(0) if email_match else "Not specified"

            # Nombre (palabras con mayúscula inicial)
            words = message.split()
            potential_names = []
            for word in words:
                if (
                    word[0].isupper()
                    and not word.isdigit()
                    and "@" not in word
                    and len(word) > 1
                    and any(c.isalpha() for c in word)
                ):
                    potential_names.append(word)

            name = (
                " ".join(potential_names[:3]) if potential_names else "Not specified"
            )

            # FECHA COMPLETA CON HORA - si el usuario la especifica
            date_text = message_lower

            # 🎯 Buscar patrones de hora: "12 PM", "3:30 PM", "14:00", "9am"
            time_pattern = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?"
            time_match = re.search(time_pattern, date_text, re.IGNORECASE)

            # Hora por defecto (10 AM)
            hour = 10
            minute = 0

            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2)) if time_match.group(2) else 0
                ampm = time_match.group(3)

                if ampm:
                    ampm = ampm.lower().replace(".", "")
                    if ampm == "pm" and hour != 12:
                        hour += 12
                    elif ampm == "am" and hour == 12:
                        hour = 0

            # Intentar parsear fecha específica
            try:
                dt = parser.parse(date_text, fuzzy=True)
                if dt.tzinfo is None:
                    dt = pytz.timezone(DEFAULT_TIMEZONE).localize(dt)
                else:
                    dt = dt.astimezone(pytz.timezone(DEFAULT_TIMEZONE))

                # Si no se especificó hora, usar la hora extraída o por defecto
                if dt.hour == 0 and dt.minute == 0:
                    dt = dt.replace(hour=hour, minute=minute)

                # Si la fecha es hoy y la hora ya pasó, mover a mañana
                now = datetime.now(pytz.timezone(DEFAULT_TIMEZONE))
                if dt.date() == now.date() and dt <= now:
                    dt = dt + timedelta(days=1)

                date_str = dt.strftime("%Y-%m-%d %H:%M")
            except:
                # Si no se puede parsear fecha específica, usar "tomorrow" con la hora extraída
                if any(
                    word in date_text
                    for word in [
                        "tomorrow",
                        "mañana",
                        "demain",
                        "morgen",
                        "domani",
                        "amanhã",
                    ]
                ):
                    base_date = "tomorrow"
                elif any(
                    word in date_text
                    for word in ["today", "hoy", "aujourd'hui", "heute", "oggi", "hoje"]
                ):
                    base_date = "today"
                else:
                    return {
                        "nombre": name,
                        "email": email,
                        "fecha": "Not specified",
                    }

                # Si encuentra hora específica, agregarla a la fecha base
                if time_match:
                    time_str = f"{hour:02d}:{minute:02d}"
                    full_date = f"{base_date} at {time_str}"
                else:
                    full_date = base_date

            logger.info(
                f"🔍 Extracción básica: nombre='{name}', email='{email}', fecha='{full_date}'"
            )
            return {"nombre": name, "email": email, "fecha": full_date}

        except Exception as e:
            logger.error(f"❌ Error en extracción básica: {e}")
            return {
                "nombre": "Not specified",
                "email": "Not specified",
                "fecha": "Not specified",
            }

    def check_language_change_request(self, message_lower, language="en"):
        """Verifica si el usuario quiere cambiar a español"""
        spanish_requests = [
            "habla en español",
            "speak in spanish",
            "parlez en espagnol",
            "spreche auf spanisch",
            "parla in spagnolo",
            "fale em espanhol",
            "quiero español",
            "want spanish",
            "prefiero español",
        ]
        return any(request in message_lower for request in spanish_requests)

    def update_conversation_state(self, state, message, extracted_data=None):
        """🔄 ACTUALIZA ESTADO DE CONVERSACIÓN - MULTILINGÜE"""
        try:
            message_lower = message.lower().strip()

            # Cambio de idioma
            if self.check_language_change_request(message_lower, state.language):
                return "language_change"

            # Datos extraídos
            if extracted_data:
                def clean(v):
                    if not v:
                        return ""
                    if isinstance(v, str) and v.lower().strip() in [
                        "not specified",
                        "no especificado",
                        "unspecified",
                    ]:
                        return ""
                    return v.strip() if isinstance(v, str) else v

                name_val = extracted_data.get("nombre") or extracted_data.get("name")
                email_val = extracted_data.get("email")
                date_val = extracted_data.get("fecha") or extracted_data.get("date")

                if clean(name_val):
                    state.data["name"] = clean(name_val)
                if clean(email_val):
                    state.data["email"] = clean(email_val)
                if clean(date_val):
                    state.data["date"] = clean(date_val)

            has_name = bool(state.data.get("name"))
            has_email = bool(state.data.get("email"))
            has_date = bool(state.data.get("date"))

            logger.info(
                f"🔄 Datos en estado → name='{state.data.get('name')}', email='{state.data.get('email')}', date='{state.data.get('date')}'"
            )

            # Flujo de booking
            booking_keywords = [
                "appointment",
                "cita",
                "schedule",
                "book",
                "agendar",
                "reservar",
                "meeting",
                "demo",
                "consultation",
                "call back",
                "phone",
                "email",
                "time",
                "today",
                "tomorrow",
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "weekend",
                "morning",
                "afternoon",
                "evening",
                "thanks",
                "please",
                "how are you",
                "how do you do",
                "good morning",
                "good afternoon",
                "good evening",
                "want",
                "would",
                "like",
                "thank",
                "rdv",
                "rendez",
                "termin",
                "appuntamento",
                "encontro",
            ]
            is_booking_intent = any(
                keyword in message_lower for keyword in booking_keywords
            )

            if state.state == "initial" and is_booking_intent and not (
                has_name or has_email or has_date
            ):
                state.state = "booking_started"
                return "booking_started"

            if not has_name:
                state.state = "waiting_name"
                return "waiting_name"
            if not has_email:
                state.state = "waiting_email"
                return "waiting_email"
            if not has_date:
                state.state = "waiting_date"
                return "waiting_date"

            state.state = "booking_completed"
            return "booking_completed"
        except Exception as e:
            logger.error(f"❌ Error actualizando estado: {e}")
            return state.state

    def get_contextual_response(self, message, from_number, language="en"):
        """💬 RESPUESTA CONTEXTUAL CON MANEJO DE ESTADO - MULTILINGÜE"""
        try:
            state = self.get_or_create_conversation_state(from_number)
            state.language = language
            message_lower = message.lower().strip()

            # Cambio de idioma
            if self.check_language_change_request(message_lower, language):
                return {
                    "message": self.get_response("language_change_spanish", language),
                    "action": "language_change",
                    "language": "es",
                }

            # Extraer datos
            extracted = self.extract_booking_data(message, language)
            logger.info(f"🔍 Datos extraídos: {extracted}")

            def clean(v):
                if not v:
                    return ""
                if isinstance(v, str) and v.lower().strip() in [
                    "not specified",
                    "no especificado",
                    "unspecified",
                ]:
                    return ""
                return v.strip() if isinstance(v, str) else v

            name = clean(extracted.get("nombre") or extracted.get("name"))
            email = clean(extracted.get("email"))
            date = clean(extracted.get("fecha") or extracted.get("date"))

            if name:
                state.data["name"] = name
            if email:
                state.data["email"] = email
            if date:
                state.data["date"] = date

            has_name = bool(state.data.get("name"))
            has_email = bool(state.data.get("email"))
            has_date = bool(state.data.get("date"))

            logger.info(f"📌 Estado actual data = {state.data}")

            # Flujo de booking
            booking_keywords = [
                "appointment",
                "cita",
                "book",
                "schedule",
                "meeting",
                "demo",
                "consultation",
                "reservar",
                "agendar",
                "rdv",
                "rendez",
                "termin",
                "appuntamento",
                "encontro",
                "want",
                "like",
                "need",
            ]
            in_booking_flow = state.state in [
                "booking_started",
                "waiting_name",
                "waiting_email",
                "waiting_date",
                "booking_completed",
            ]
            starts_booking = any(k in message_lower for k in booking_keywords)

            if starts_booking or in_booking_flow:
                if state.state == "initial":
                    state.state = "booking_started"

                if not has_name:
                    state.state = "waiting_name"
                    return {
                        "message": self.get_response(
                            "data_extraction_request", language
                        ),
                        "action": "request_name",
                        "extracted_data": state.data,
                    }

                if not has_email:
                    state.state = "waiting_email"
                    return {
                        "message": self.get_response(
                            "ask_for_email", language, name=state.data.get("name", "")
                        ),
                        "action": "request_email",
                        "extracted_data": state.data,
                    }

                if not has_date:
                    state.state = "waiting_date"
                    return {
                        "message": self.get_response(
                            "ask_for_date", language, name=state.data.get("name", "")
                        ),
                        "action": "request_date",
                        "extracted_data": state.data,
                    }

                state.state = "booking_completed"
                return {
                    "message": self.get_response(
                        "data_extracted_success",
                        language,
                        name=state.data.get("name", "N/A"),
                        email=state.data.get("email", "N/A"),
                        date=state.data.get("date", "N/A"),
                    ),
                    "action": "proceed_booking",
                    "extracted_data": state.data,
                }

            return {
                "message": self.get_response("generic_response", language),
                "action": "generic",
            }
        except Exception as e:
            logger.error(f"❌ Error en respuesta contextual: {e}")
            return {
                "message": self.get_response("generic_response", language),
                "action": "error",
            }

    def send_whatsapp_message(self, to_number, message):
        """Envía mensaje por WhatsApp"""
        try:
            url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json"
            data = {
                "From": f"whatsapp:{TWILIO_PHONE_NUMBER}",
                "To": f"whatsapp:{to_number}",
                "Body": message,
            }
            response = requests.post(
                url, data=data, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
            )

            if response.status_code == 201:
                logger.info(f"✅ Mensaje enviado a {to_number}")
                return True
            else:
                error_text = response.text
                if "unverified number" in error_text.lower():
                    logger.error(f"❌ MODO TRIAL: Número no verificado {to_number}")
                    self.send_whatsapp_message(
                        to_number, self.get_response("trial_mode_warning", "en")
                    )
                else:
                    logger.error(
                        f"❌ Error enviando mensaje: {response.status_code} - {error_text}"
                    )
                return False
        except Exception as e:
            logger.error(f"❌ Error enviando mensaje WhatsApp: {e}")
            return False


# ========================================
# 🚀 FLASK APPLICATION
# ========================================
app = Flask(__name__)
agent = WhatsAppVoiceAgent()

# ========================================
# ⭐ CORRECCIÓN CRÍTICA: FUNCION NORMALIZAR FECHAS CON HORA
# ========================================
def normalize_date_to_iso(date_text, timezone="America/New_York"):
    """🛠️ Convierte texto natural en fecha ISO EXACTA para Cal.com

    IMPORTANTE: Cal.com REQUIERE formato exacto YYYY-MM-DDTHH:MM:SSZ (en UTC)
    """
    try:
        if not date_text or not isinstance(date_text, str):
            logger.error("❌ Fecha inválida o vacía")
            return None

        date_text = date_text.strip()
        tz = pytz.timezone(timezone)
        now = datetime.now(tz)

        # 🎯 EXTRAER HORA ESPECÍFICA si el usuario la menciona
        time_pattern = r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?"
        time_match = re.search(time_pattern, date_text, re.IGNORECASE)

        # Hora por defecto (10 AM)
        hour = 10
        minute = 0

        if time_match:
            hour = int(time_match.group(1))
            minute = int(time_match.group(2)) if time_match.group(2) else 0
            ampm = time_match.group(3).lower() if time_match.group(3) else None

            # Convertir de 12h a 24h
            if ampm:
                if ampm.startswith("p") and hour != 12:
                    hour += 12
                elif ampm.startswith("a") and hour == 12:
                    hour = 0

        # 1️⃣ Parsear fecha natural
        date_lower = date_text.lower()
        if any(
            word in date_lower
            for word in [
                "tomorrow",
                "mañana",
                "demain",
                "morgen",
                "domani",
                "amanhã",
            ]
        ):
            # Mañana = día siguiente a medianoche
            dt = now + timedelta(days=1)
            dt = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        elif any(
            word in date_lower
            for word in ["today", "hoy", "aujourd'hui", "heute", "oggi", "hoje"]
        ):
            # Hoy = hoy a la hora especificada, pero si ya pasó, usar mañana
            dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if dt <= now:
                dt = dt + timedelta(days=1)
        else:
            try:
                # Intentar parsear fecha específica
                dt = parser.parse(date_text, fuzzy=True)
                if dt.tzinfo is None:
                    dt = tz.localize(dt)
                else:
                    dt = dt.astimezone(tz)

                # Si no se especificó hora, usar la hora extraída o por defecto
                if dt.hour == 0 and dt.minute == 0:
                    dt = dt.replace(hour=hour, minute=minute)

                # Si la fecha es hoy y la hora ya pasó, mover a mañana
                if dt.date() == now.date() and dt <= now:
                    dt = dt + timedelta(days=1)
            except:
                logger.error(f"❌ No se pudo parsear la fecha: {date_text}")
                return None

        # 2️⃣ Asegurar que la fecha sea FUTURA
        if dt <= now:
            logger.warning(f"⚠️ Fecha {dt} es pasada, moviendo a mañana")
            dt = now + timedelta(days=1)
            dt = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)

        # 3️⃣ Convertir a UTC
        dt_utc = dt.astimezone(pytz.utc)

        # 4️⃣ Formato EXACTO que Cal.com requiere: YYYY-MM-DDTHH:MM:SSZ
        iso_date = dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ")

        logger.info(f"📅 Fecha convertida: '{date_text}' → {iso_date}")
        return iso_date

    except Exception as e:
        logger.error(f"❌ Error normalizando fecha '{date_text}': {e}")
        return None


# ========================================
# 🎵 MANEJO DE MENSAJES DE VOZ
# ========================================
def handle_voice_message(audio_url, from_number, language="en"):
    """Maneja mensajes de voz"""
    try:
        logger.info("🎤 Procesando mensaje de voz...")
        response = requests.get(
            audio_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        )

        if response.status_code != 200:
            logger.error(f"❌ Error descargando audio: {response.status_code}")
            return agent.get_response("generic_response", language)

        audio_data = response.content
        temp_filename = None

        try:
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as temp_file:
                temp_file.write(audio_data)
                temp_filename = temp_file.name

            # Transcribir con OpenAI Whisper
            with open(temp_filename, "rb") as audio_file:
                transcription_result = client.audio.transcriptions.create(
                    model="whisper-1", file=audio_file
                )

            transcribed_text = (transcription_result.text or "").strip()
            logger.info(f"📝 Texto extraído: {transcribed_text}")

            # Detectar idioma y procesar
            detected_language = agent.detect_language(transcribed_text)
            response_data = agent.get_contextual_response(
                transcribed_text, from_number, detected_language
            )

            # Enviar respuesta
            if "message" in response_data:
                agent.send_whatsapp_message(from_number, response_data["message"])

            return transcribed_text
        finally:
            if temp_filename and os.path.exists(temp_filename):
                try:
                    os.unlink(temp_filename)
                    logger.info(f"🗑️ Archivo temporal limpiado")
                except:
                    pass
    except Exception as e:
        logger.error(f"❌ Error procesando mensaje de voz: {e}")
        return agent.get_response("generic_response", language)


# ========================================
# 📅 API DE CAL.COM - VERSIÓN CORREGIDA Y VALIDADA VERSION OPTIMIZADA PARA 
# ANTICIPAR CITAS CADA 60 MIN  EN CAL.COM 
# ========================================
def create_cal_com_booking(
    name, email, date_preference, phone_number, language="en", retry_count=0
):
    """🛠️ Crea cita en Cal.com - VERSIÓN FINAL Y ESTABLE"""
    
    MAX_RETRIES = 3
    
    try:
        logger.info("📅 Iniciando creación de cita en Cal.com...")
        
        # ===== 1️⃣ VALIDACIÓN DE DATOS =====
        if not CAL_API_KEY:
            return {"success": False, "error": "Falta CAL_API_KEY"}
        if not name or len(name.strip()) < 2:
            return {"success": False, "error": "Nombre inválido"}
        if not email or "@" not in email:
            return {"success": False, "error": f"Email inválido: {email}"}
        if not date_preference:
            return {"success": False, "error": "Fecha no especificada"}
        
        # ===== 2️⃣ NORMALIZAR FECHA =====
        iso_date = normalize_date_to_iso(date_preference)
        if not iso_date:
            return {"success": False, "error": f"No se pudo parsear: {date_preference}"}
        
        # ===== 🔥 VALIDACIÓN CRÍTICA: ANTECEDENCIA MÍNIMA =====
        now_utc = datetime.now(pytz.utc)
        booking_dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        hours_diff = (booking_dt - now_utc).total_seconds() / 3600
        
        MINIMUM_NOTICE_HOURS = 1  # Debe coincidir con tu configuración en Cal.com
        
        if hours_diff < MINIMUM_NOTICE_HOURS:
            logger.warning(f"⚠️ Reserva muy cercana: {hours_diff:.1f}h < {MINIMUM_NOTICE_HOURS}h")
            
            valid_dt = now_utc + timedelta(hours=MINIMUM_NOTICE_HOURS)
            local_tz = pytz.timezone(DEFAULT_TIMEZONE)
            valid_local = valid_dt.astimezone(local_tz)
            pretty_time = valid_local.strftime("%I:%M %p")
            
            if valid_local.date() == now_utc.astimezone(local_tz).date():
                suggested_time = f"today at {pretty_time}"
            else:
                suggested_time = "tomorrow at 10 AM"
            
            return {
                "success": False,
                "error": "Antecedencia insuficiente",
                "message": agent.get_response(
                    "insufficient_notice_error",
                    language,
                    requested_time=date_preference,
                    minimum_hours=MINIMUM_NOTICE_HOURS,
                    suggested_time=suggested_time,
                    pretty_time=pretty_time
                )
            }
        
        # ===== 3️⃣ OBTENER DURACIÓN DEL EVENTO =====
        event_duration_minutes = 15  # CAMBIA ESTO según tu evento
        
        # ===== 4️⃣ CALCULAR FECHA DE FIN =====
        start_dt = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
        end_dt = start_dt + timedelta(minutes=event_duration_minutes)
        end_iso = end_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # ===== 5️⃣ PAYLOAD =====
        state = agent.get_or_create_conversation_state(phone_number)
        booking_language = getattr(state, "language", "en")
        language_map = {"es": "es", "en": "en", "fr": "fr", "de": "de", "it": "it", "pt": "pt"}
        
        payload = {
            "eventTypeId": CAL_EVENT_TYPE_ID,
            "start": iso_date,
            "end": end_iso,
            "timeZone": DEFAULT_TIMEZONE,
            "language": language_map.get(booking_language, "en"),
            "responses": {
                "name": name.strip(),
                "email": email.strip(),
                "notes": f"WhatsApp: {phone_number}",
            },
            "location": "Google Meet",
            "metadata": {
                "source": "WhatsApp Voice Agent",
                "phone_number": phone_number,
                "language": booking_language,
            },
            "status": "ACCEPTED",
        }
        
        headers = {
            "Authorization": f"Bearer {CAL_API_KEY}",
            "Content-Type": "application/json",
            "cal-api-version": "2024-06-14",
        }
        
        url = "https://api.cal.com/v2/bookings"
        
        logger.info("🌐 Enviando solicitud a Cal.com...")
        logger.info(f"📨 Payload: {json.dumps(payload, indent=2)}")
        
        # ===== 6️⃣ ENVIAR SOLICITUD =====
        response = requests.post(url, json=payload, headers=headers)
        
        # ===== 7️⃣ MANEJO DE RESPUESTA =====
        logger.info(f"📥 Status Code: {response.status_code}")
        
        if response.status_code in [200, 201]:
            data = response.json()
            
            # 🔍 BUSCAR EL ID EN TODOS LOS LUGARES POSIBLES
            booking_id = None
            
            # Cal.com v2 usa 'uid' en el root
            booking_id = data.get("uid") or data.get("id")
            
            # Si no está en root, buscar en data.booking
            if not booking_id and "data" in data and isinstance(data["data"], dict):
                booking_id = data["data"].get("uid") or data["data"].get("id")
            
            # Si aún no lo encontramos, buscar recursivamente
            if not booking_id:
                def deep_search(obj, path=""):
                    nonlocal booking_id
                    if isinstance(obj, dict):
                        for k, v in obj.items():
                            if k in ["uid", "id", "bookingId"] and v and isinstance(v, str):
                                booking_id = v
                                logger.info(f"✅ Encontrado booking_id en {path}.{k}: {booking_id}")
                                return True
                            if isinstance(v, (dict, list)):
                                if deep_search(v, f"{path}.{k}" if path else k):
                                    return True
                    elif isinstance(obj, list):
                        for i, item in enumerate(obj):
                            if deep_search(item, f"{path}[{i}]"):
                                return True
                    return False
                
                deep_search(data)
            
            # Construir la URL
            if booking_id:
                meeting_url = f"https://app.cal.com/booking/{booking_id}"
                logger.info(f"✅ URL final: {meeting_url}")
            else:
                # Fallback a la URL del evento
                meeting_url = f"https://app.cal.com/{os.getenv('ACCOUNT_USERNAME', '')}/{CAL_EVENT_TYPE_ID}"
                logger.warning(f"⚠️ No se encontró booking_id, usando URL del evento: {meeting_url}")
            
            return {
                "success": True,
                "booking_id": booking_id,
                "booking_url": data.get("uri", ""),
                "meeting_url": meeting_url,
                "raw": data,
            }
            
        elif response.status_code == 400:
            error_text = response.text
            logger.error(f"❌ Error Cal.com → Status: {response.status_code}")
            
            if "no_available_users_found" in error_text:
                logger.warning(f"⚠️ Slot ocupado: {iso_date}, buscando siguiente...")
                if retry_count >= MAX_RETRIES:
                    return {
                        "success": False,
                        "error": "Máximos reintentos",
                        "message": agent.get_response("all_slots_full", language)
                    }
                
                next_slot = get_next_available_slot(iso_date)
                if not next_slot:
                    return {
                        "success": False,
                        "error": "No hay slots",
                        "message": agent.get_response("availability_error", language)
                    }
                
                agent.send_whatsapp_message(
                    phone_number,
                    agent.get_response("slot_conflict_retry", language, original_time=iso_date, new_time=next_slot)
                )
                
                return create_cal_com_booking(
                    name=name, email=email, date_preference=next_slot,
                    phone_number=phone_number, language=language, retry_count=retry_count + 1
                )
                
            elif "booking_time_out_of_bounds" in error_text:
                logger.error(f"❌ Fuera de límites: {iso_date}")
                
                try:
                    new_preference = f"tomorrow at {date_preference.split(' at ')[1]}" if " at " in date_preference else "tomorrow at 10 AM"
                    agent.send_whatsapp_message(
                        phone_number,
                        agent.get_response("time_out_of_bounds_error", language, requested_time=date_preference, next_available=new_preference)
                    )
                    
                    return create_cal_com_booking(
                        name=name, email=email, date_preference=new_preference,
                        phone_number=phone_number, language=language, retry_count=retry_count
                    )
                except:
                    return {
                        "success": False,
                        "error": "Time out of bounds",
                        "message": agent.get_response("time_out_of_bounds_error", language)
                    }
            
            else:
                return {
                    "success": False,
                    "error": f"Cal.com API Error ({response.status_code})",
                    "message": error_text
                }
            
    except Exception as e:
        logger.error(f"❌ Excepción: {e}")
        return {"success": False, "error": f"Exception: {str(e)}"}
# ====================================================
#  CONSULTA API PARA PROXIMA CITA DISPONIBLE
#  SI EL SLOT SOLICITADO ESTA OCUPADO
# ====================================================
def get_next_available_slot(current_iso_date, timezone="America/New_York"):
    """🔍 Consulta la API de Cal.com para encontrar el siguiente slot libre"""
    try:
        # Convertir ISO a datetime
        current_dt = datetime.fromisoformat(current_iso_date.replace("Z", "+00:00"))

        # Buscar slots para los próximos 7 días
        start_date = (current_dt + timedelta(minutes=15)).strftime(
            "%Y-%m-%d"
        )  # 15 min después
        end_date = (current_dt + timedelta(days=7)).strftime("%Y-%m-%d")

        availability_url = f"https://api.cal.com/v1/availability "

        params = {
            "apiKey": CAL_API_KEY,
            "eventTypeId": CAL_EVENT_TYPE_ID,
            "startDate": start_date,
            "endDate": end_date,
            "timeZone": timezone,
        }

        logger.info(f"🔍 Consultando disponibilidad: {availability_url}")
        logger.info(f"📊 Parámetros: {json.dumps(params, indent=2)}")

        response = requests.get(availability_url, params=params)

        if response.status_code != 200:
            logger.error(
                f"❌ Error consultando disponibilidad: {response.status_code}"
            )
            return None

        data = response.json()
        slots = data.get("slots", [])

        # Buscar el primer slot disponible
        for day_slots in slots:
            if day_slots.get("available", False):
                # Devolver el primer slot del día
                first_slot = day_slots.get("slots", [])[0]
                if first_slot:
                    logger.info(f"✅ Próximo slot disponible: {first_slot}")
                    return first_slot

        logger.warning("⚠️ No se encontraron slots disponibles en los próximos 7 días")
        return None

    except Exception as e:
        logger.error(f"❌ Error en get_next_available_slot: {e}")
        return None


# ==========================================
#    MANEJO DE MENSAJES DE WHATSAPP
# ============================================
@app.route("/webhook/whatsapp", methods=["POST"])
def whatsapp_webhook():
    """Webhook de WhatsApp"""
    try:
        form_data = request.form.to_dict()
        from_number = form_data.get("From", "").replace("whatsapp:", "")
        message_body = form_data.get("Body", "").strip()
        media_url = form_data.get("MediaUrl0", "")

        logger.info(f"📱 Mensaje de WhatsApp: {from_number}")
        logger.info(f"📝 Texto: '{message_body}'")

        # 🎤 MENSAJE DE AUDIO
        if media_url:
            logger.info(f"🎵 Audio: {media_url}")
            result = handle_voice_message(media_url, from_number)
            return jsonify({"status": "success", "message": "Voice message processed"})

        # ✉️ MENSAJE DE TEXTO
        if message_body:
            detected_language = agent.detect_language(message_body)
            response_data = agent.get_contextual_response(
                message_body, from_number, detected_language
            )

            # Cambio de idioma
            if response_data.get("action") == "language_change":
                state = agent.get_or_create_conversation_state(from_number)
                state.language = response_data.get("language", "es")

            # 📅 PROCESO DE RESERVA
            if response_data.get("action") == "proceed_booking":
                state = agent.get_or_create_conversation_state(from_number)

                # VALIDAR ANTES DE ENVIAR
                if not all(
                    [state.data.get("name"), state.data.get("email"), state.data.get("date")]
                ):
                    error_msg = "❌ Faltan datos requeridos. Necesito nombre, email y fecha."
                    agent.send_whatsapp_message(from_number, error_msg)
                    return jsonify(
                        {"status": "error", "message": "Missing required fields"}
                    )

                booking_result = create_cal_com_booking(
                    name=state.data.get("name"),
                    email=state.data.get("email"),
                    date_preference=state.data.get("date"),
                    phone_number=from_number,
                )

                if booking_result.get("success"):
                    meeting_url = booking_result.get("meeting_url", "")
                    success_message = agent.get_response(
                        "appointment_scheduled",
                        detected_language,
                        meeting_url=meeting_url,
                    )
                    agent.send_whatsapp_message(from_number, success_message)

                    # Guardar en Google Sheets (opcional)
                    agent.sheets_integration.save_booking_data(
                        phone_number=from_number,
                        nombre=state.data.get("name", ""),
                        email=state.data.get("email", ""),
                        fecha_cita=state.data.get("date", ""),
                        idioma=detected_language,
                        notas=f"Booking ID: {booking_result.get('booking_id')}, Meeting URL: {meeting_url}",
                    )

                    # Limpiar estado
                    if from_number in agent.conversation_states:
                        del agent.conversation_states[from_number]
                else:
                    # Mostrar error detallado
                    error_msg = f"❌ {booking_result.get('message', 'Error desconocido')}"
                    if booking_result.get("details"):
                        error_msg += f"\n\nDetalles: {booking_result['details']}"

                    # Si el error es de fecha pasada, dar mensaje específico
                    if "past" in str(booking_result.get("details", "")).lower():
                        error_msg = agent.get_response("past_date_error", detected_language)

                    agent.send_whatsapp_message(from_number, error_msg)
                    logger.error(f"❌ Error detallado: {booking_result}")

            # 💬 RESPUESTA NORMAL
            else:
                if "message" in response_data:
                    agent.send_whatsapp_message(from_number, response_data["message"])

            return jsonify({"status": "success", "message": "Text message processed"})

    except Exception as e:
        logger.error(f"❌ Error general en webhook: {e}", exc_info=True)
        return jsonify(
            {"status": "error", "message": "Internal error", "error": str(e)}
        )


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    return jsonify(
        {
            "status": "healthy",
            "agent": "WhatsApp Voice Agent - MULTILINGÜE PRODUCTION READY",
            "version": "9.0",
            "timezone": DEFAULT_TIMEZONE,
            "default_language": "en",
            "supported_languages": ["es", "en", "fr", "de", "it", "pt"],
            "features": [
                "Voice transcription (OpenAI Whisper)",
                "Language detection (6 idiomas)",
                "Cal.com API v2 integration",
                "Timezone: America/New_York",
                "Google Sheets (opcional)",
                "Trial mode support",
                "Smart time extraction",
                "Past date validation",
            ],
            "credentials": {
                "Twilio": bool(TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN),
                "OpenAI": bool(OPENAI_API_KEY),
                "Cal.com": bool(CAL_API_KEY),
                "Google Sheets": GOOGLE_SHEETS_AVAILABLE,
            },
        }
    )


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("🤖 WHATSAPP VOICE AGENT - MULTILINGÜE INICIANDO")
    print("=" * 70)
    print(f"⏰ Zona horaria: {DEFAULT_TIMEZONE}")
    print(f"🌍 Idioma por defecto: English")
    print(f"📅 Event Type ID: {CAL_EVENT_TYPE_ID}")
    print(
        f"💾 Google Sheets: {'✅ Activado' if GOOGLE_SHEETS_AVAILABLE else '⚠️  Opcional (no instalado)'}"
    )
    print(f"🌐 Idiomas soportados: Español, English, Français, Deutsch, Italiano, Português")
    print("=" * 70)
    print("🚀 Servidor corriendo en http://0.0.0.0:5000   ")
    print("📡 Webhook: http://localhost:5000/webhook/whatsapp")
    print("🌐 Health: http://localhost:5000/health")
    print("=" * 70 + "\n")

    app.run(host="0.0.0.0", port=5000, debug=False)
 