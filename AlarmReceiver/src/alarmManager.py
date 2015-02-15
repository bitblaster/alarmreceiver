#!/usr/bin/python
# -*- coding: utf-8 -*- 

import logging
import threading
import re
import smtplib
from email.mime.text import MIMEText

class AlarmManager:
    # CL-OP -> Inserimento totale
    # NL-OP -> Inserimento parziale
    # BC -> Reset memoria
    # JP -> Riconoscimento codice/chiave
    # XT-XR -> Resistenza interna batteria
    # YM-YR -> Corto circuito/disconnessione batteria
    # YT-YR -> Batteria inefficiente
    # AT-AR -> Mancanza alimentazione
    # EM-EN -> Scomparsa dispositivo
    # DD-DR -> Codice/chiave errati
    # LB-LX -> Ingresso programmazione
    # OU-OV -> Malfunzionamento uscita
    errorCodes = {
          # Non definito
          "UX" : {"desc": "Non definito", "sendSms": NEVER},
          
          # Set
          "BA" : {"desc": "ALLARME INTRUSIONE", "sendSms": ALWAYS},
          "TA" : {"desc": "SABOTAGGIO", "sendSms": IF_ACTIVE},
          "BB" : {"desc": "Esclusione", "sendSms": NEVER},
          "CL" : {"desc": "Inserimento totale", "sendSms": NEVER},
          "NL" : {"desc": "Inserimento parziale", "sendSms": NEVER},
          "BC" : {"desc": "Reset memoria", "sendSms": NEVER},
          "JP" : {"desc": "Riconoscimento codice/chiave", "sendSms": NEVER},
          "XT" : {"desc": "Resistenza interna batteria", "sendSms": IF_ACTIVE},
          "YM" : {"desc": "Corto circuito/disconnessione batteria", "sendSms": IF_ACTIVE},
          "YT" : {"desc": "Batteria inefficiente", "sendSms": IF_ACTIVE},
          "AT" : {"desc": "Mancanza alimentazione", "sendSms": IF_ACTIVE},
          "EM" : {"desc": "Scomparsa dispositivo", "sendSms": IF_ACTIVE},
          "DD" : {"desc": "Codice/chiave errati", "sendSms": ALWAYS},
          "LB" : {"desc": "Ingresso programmazione", "sendSms": IF_ACTIVE},
          "OU" : {"desc": "Malfunzionamento uscita", "sendSms": EMAIL_FALLBACK},
          
          # Reset
          "BR" : {"desc": "Ripristino allarme intrusione", "sendSms": ALWAYS},
          "TR" : {"desc": "Ripristino sabotaggio", "sendSms": ALWAYS},
          "BU" : {"desc": "Ripristino esclusione", "sendSms": NEVER},
          "OP" : {"desc": "Disinserimento", "sendSms": NEVER},
          "XR" : {"desc": "Ripristino resistenza interna batteria", "sendSms": IF_ACTIVE},
          "YR" : {"desc": "Ripristino batteria", "sendSms": IF_ACTIVE},
          "AR" : {"desc": "Ripristino alimentazione", "sendSms": IF_ACTIVE},
          "EN" : {"desc": "Ripristino scomparsa dispositivo", "sendSms": IF_ACTIVE},
          "DR" : {"desc": "Ripristino codice/chiave errati", "sendSms": ALWAYS},
          "LX" : {"desc": "Uscita programmazione", "sendSms": IF_ACTIVE},
          "OV" : {"desc": "Ripristino malfunzionamento uscita", "sendSms": EMAIL_FALLBACK}
    }

    def __init__(self):
        self.alarmActive = False
        self.threadLock = threading.Lock()
        
    def manageAlarmMessage(msg):
        m = alarmPattern.search(msg)
        if m:
            tipo = m.group(1)
            desc = m.group(2).strip()
            logging.info("Tipo evento: " + tipo + ", testo: " + desc)
            
            if tipo == "CL" or tipo == "NL":
                self.alarmActive = True
                for groupId in config['groupsToSwitchOffWhenActive']:
                    callPiServer("allOff/group:" + groupId)
            elif tipo == "OP":
                self.alarmActive = False
                for groupId in config['groupsToSwitchOnWhenDeactive']:
                    callPiServer("allOn/group:" + groupId)
                
            errorCode = errorCodes[tipo]
            if errorCode:
                msgString = errorCode["desc"] + ": " + desc
            else:
                msgString = "Evento sconosciuto: " + errorCode[0] + ": " + desc
            
            smsPolicy = errorCode["sendSms"]
            
            if smsPolicy == ALWAYS or smsPolicy == IF_ACTIVE and self.alarmActive:
                AlarmTCPHandler.sendSms(msgString)
                
            mailSent = AlarmTCPHandler.sendEmail(errorCode["desc"], msgString)
            if not mailSent and smsPolicy == EMAIL_FALLBACK:
                AlarmTCPHandler.sendSms(msgString)
                
    @staticmethod
    def sendEmail(subject, msg):
        # Get lock to synchronize threads
        logging.debug("Acquiring lock...")
        threadLock.acquire()
        
        logging.info("Invio email: " + msg)
        try:
            msg = MIMEText(msg)

            mailFrom = "Allarme Casa <videomozzi@gmail.com>"
            mailTo = "Roberto Mozzicato <bitblasters@gmail.com>"
            msg['Subject'] = subject
            msg['From'] = mailFrom
            msg['To'] = mailTo
    
            server = smtplib.SMTP('smtp.gmail.com:587')
            server.starttls()
            server.login("videomozzi","pwdmarcia*123")
            server.sendmail(mailFrom, [mailTo], msg.as_string())
            server.quit()
            
            return True
        except Exception as e:
            logging.error("Errore durante l'invio dell'email: " + str(e))
            return False
        finally:        
            # Free lock to release next thread
            threadLock.release()
            logging.debug("Lock released!")
        
    @staticmethod
    def sendSms(msg):
        logging.info("Invio sms: " + msg)
        if DISABLE_SMS:
            logging.info("sms disabled")
            return
        
        # Get lock to synchronize threads
        logging.debug("Acquiring lock...")
        threadLock.acquire()
        
        # Free lock to release next thread
        threadLock.release()
        logging.debug("Lock released!")
    
    @staticmethod
    def callPiServer(requestString):
        requestString = requestString + "?client=raspberry&time=" + str(int(time.time()));
        conn = httplib.HTTPConnection(config['pi_server_url'])
        conn.request("GET", "/" + encrypt(requestString))
        r1 = conn.getresponse()
        print(r1.status, r1.reason)
        
    @staticmethod
    def encrypt(message):
        cipher = Blowfish.new(config['encrypt_passphrase'], Blowfish.MODE_CBC, config['encrypt_iv'])
        pad = 8-(len(message)%8)
        for x in range(pad):
            message+=" "
        encrypted = cipher.encrypt(message)
        return base64.urlsafe_b64encode(encrypted)
