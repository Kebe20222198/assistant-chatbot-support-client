import uuid
from loguru import logger
from app.models.schemas import TicketCreate

class TicketService:
    @staticmethod
    def create_ticket(ticket_data: TicketCreate) -> str:
        """
        Simule la création d'un ticket dans la base de données (ex: Postgres)
        et l'envoi d'une notification sur Slack/Teams.
        """
        # 1. Création du ticket (Simulation BDD)
        ticket_id = f"TCK-{str(uuid.uuid4())[:8].upper()}"
        
        logger.info(f"💾 [DB MOCK] Ticket enregistré en base : {ticket_id}")
        logger.debug(f"Détails du ticket : Utilisateur {ticket_data.user_email}, Statut: {ticket_data.status}")
        
        # 2. Envoi de la notification (Simulation Webhook)
        TicketService._send_slack_notification(ticket_id, ticket_data.user_email, ticket_data.conversation_history)
        
        return ticket_id

    @staticmethod
    def _send_slack_notification(ticket_id: str, email: str, conversation: str):
        """
        Simule une requête HTTP vers un Webhook Slack.
        """
        logger.info("🔔 [SLACK MOCK] Envoi de la notification au canal #support-escalade...")
        message = (
            f"🔴 *Nouveau Ticket Escaladé : {ticket_id}*\n"
            f"👤 Utilisateur : {email or 'Anonyme'}\n"
            f"🤖 Raison : Le bot n'a pas trouvé la réponse dans la doc.\n"
            f"💬 Dernier message : {conversation.split()[-1] if conversation else '...'}\n"
            f"👉 [Cliquez ici pour répondre]"
        )
        logger.info(f"Slack Payload:\n{message}")
