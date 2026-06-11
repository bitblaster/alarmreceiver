#!/usr/bin/python
# -*- coding: utf-8 -*-

import sys
import logging
import re
import time
import socket
import binascii
import json
import threading
import yaml
import paho.mqtt.client as mqtt

# ---------------------------------------------------------------------------
# Costanti stato allarme (valori attesi da Home Assistant)
# ---------------------------------------------------------------------------
STATE_DISARMED   = "disarmed"
STATE_ARMED_AWAY = "armed_away"
STATE_ARMED_HOME = "armed_home"

# ---------------------------------------------------------------------------
# Topic MQTT pubblicati verso Home Assistant
# ---------------------------------------------------------------------------
TOPIC_STARTUP    = "alarm/startup"       # started
TOPIC_STATE      = "alarm/state"         # armed_away | armed_home | disarmed | triggered
TOPIC_POWER      = "alarm/fault/power"   # ON / OFF
TOPIC_BATTERY    = "alarm/fault/battery" # ON / OFF
TOPIC_DEVICE     = "alarm/fault/device"  # ON / OFF
TOPIC_OUTPUT     = "alarm/fault/output"  # ON / OFF
TOPIC_BYPASS     = "alarm/fault/bypass"  # ON / OFF
TOPIC_LAST_EVENT = "alarm/last_event"    # testo libero dell'ultimo evento
TOPIC_ATTRIBUTES = "alarm/attributes"    # OK | elenco zone aperte
TOPIC_COMMAND    = "alarm/command"       # DISARM | ARM_AWAY | ARM_HOME  (da HA)

# ---------------------------------------------------------------------------
# Timeout / delay comunicazione TCP con la centralina
# ---------------------------------------------------------------------------
TIMEOUT_SEC    = 2
DELAY_BETWEEN  = 0.5   # secondi tra i messaggi di uno stesso comando


# ===========================================================================
# Strutture dati per i comandi TCP verso la centralina
# ===========================================================================
class TcpCommand:
    """Un singolo passo di un comando TCP: bytes da inviare + risposta attesa."""
    def __init__(self, send: str, expect: str):
        self.send   = send    # stringa hex
        self.expect = expect  # stringa hex | "*" (qualsiasi) | "" (nessuna risposta attesa)


class Zone:
    """Zona della centralina usata per zone_verify."""
    def __init__(self, description: str, byte: int, value: int):
        self.description = description
        self.byte        = byte
        self.value       = value


# ===========================================================================
# Configurazione applicazione (unica fonte: /etc/alarmReceiver.yaml)
# ===========================================================================
class AppConfig:
    """Carica e valida l'intera configurazione dal file YAML."""

    DEFAULT_PATH = '/etc/alarmReceiver.yaml'

    def __init__(self, data: dict):
        mqtt_cfg = data.get('mqtt', {})
        self.mqtt_broker   = mqtt_cfg['broker']
        self.mqtt_port     = int(mqtt_cfg.get('port', 1883))
        self.mqtt_user     = mqtt_cfg['user']
        self.mqtt_password = mqtt_cfg['password']

        alarm = data.get('alarm_system', {})
        self.alarm_ip   = alarm['ip']
        self.alarm_port = int(alarm['port'])

        server = data.get('server', {})
        self.server_port = int(server['port'])

        self.commands = {}
        for cmd_group in data.get('commands', []):
            for name, steps in cmd_group.items():
                self.commands[name] = [TcpCommand(**step) for step in steps]

        self.zones = [Zone(**z) for z in data.get('zones', [])]

    @classmethod
    def load(cls, path: str = DEFAULT_PATH) -> 'AppConfig':
        logging.info(f'Caricamento configurazione da {path}')
        with open(path, 'r', encoding='utf-8') as f:
            data = yaml.safe_load(f)
        return cls(data)


# ===========================================================================
# Esecutore comandi TCP
# ===========================================================================
class TcpCommandExecutor:
    """
    Esegue una sequenza di passi TCP verso la centralina INIM e
    restituisce (success, last_response).
    """

    def __init__(self, ip: str, port: int):
        self.ip   = ip
        self.port = port

    def run(self, steps: list) -> tuple:
        """
        Esegue tutti i passi di un comando.
        Ritorna (True, last_response) se tutti i passi hanno esito positivo,
        (False, last_response) al primo errore.
        """
        success       = False
        last_response = b""

        try:
            with socket.create_connection((self.ip, self.port), timeout=TIMEOUT_SEC) as s:
                for i, step in enumerate(steps):
                    to_send = binascii.unhexlify(step.send)
                    logging.debug(f"TCP step {i+1} → send: {step.send}  expect: {step.expect}")
                    s.sendall(to_send)

                    if step.expect:
                        try:
                            s.settimeout(TIMEOUT_SEC)
                            response      = s.recv(4096)
                            last_response = response
                            actual        = binascii.hexlify(response).decode()
                            logging.debug(f"TCP step {i+1} ← recv: {actual}")

                            if step.expect == "*":
                                success = True
                            elif actual.lower() == step.expect.lower():
                                success = True
                            else:
                                logging.warning(f"TCP mismatch step {i+1}: got {actual}, expected {step.expect}")
                                return False, last_response

                        except socket.timeout:
                            logging.error(f"TCP timeout at step {i+1}")
                            return False, last_response
                    else:
                        success = True   # nessuna risposta attesa → OK

                    time.sleep(DELAY_BETWEEN)

        except Exception as e:
            logging.error(f"TCP connection error: {e}")
            return False, last_response

        return success, last_response


# ===========================================================================
# AlarmManager
# ===========================================================================
class AlarmManager:
    alarmPattern = re.compile(r"\[#[0-9]{6}\|....(..)([0-9]+)\^?([^\^]*)\^?\]")

    def __init__(self):
        # Client MQTT persistente (connesso una volta sola)
        self._mqttClient = None

        # Componenti TCP (popolati in start())
        self._tcpExecutor = None
        self._commands    = {}
        self._zones       = []

        # Mappa eventi SIA-IP → handler
        self.reactions = {
            "UX": {"subject": "Non definito",                            "execute": None},

            # Stato allarme
            "BA": {"subject": "ALLARME INTRUSIONE",                     "execute": self.allarmeIntrusione},
            "TA": {"subject": "SABOTAGGIO",                             "execute": self.sabotaggio},
            "CL": {"subject": "Inserimento totale",                     "execute": self.inserimentoTotale},
            "NL": {"subject": "Inserimento parziale",                   "execute": self.inserimentoParziale},
            "OP": {"subject": "Disinserimento",                         "execute": self.disinserimento},
            "BR": {"subject": "Ripristino allarme intrusione",          "execute": self.ripristinoAllarme},
            "TR": {"subject": "Ripristino sabotaggio",                  "execute": self.ripristinoAllarme},

            # Alimentazione
            "AT": {"subject": "Mancanza alimentazione",                 "execute": self.faultPowerOn},
            "AR": {"subject": "Ripristino alimentazione",               "execute": self.faultPowerOff},

            # Batteria
            "YM": {"subject": "Corto circuito/disconnessione batteria", "execute": self.faultBatteryOn},
            "YT": {"subject": "Batteria inefficiente",                  "execute": self.faultBatteryOn},
            "YR": {"subject": "Ripristino batteria",                    "execute": self.faultBatteryOff},
            "XT": {"subject": "Resistenza interna batteria",            "execute": self.onlyLastEvent},
            "XR": {"subject": "Ripristino resistenza interna batteria", "execute": self.onlyLastEvent},

            # Dispositivi
            "EM": {"subject": "Scomparsa dispositivo",                  "execute": self.faultDeviceOn},
            "EN": {"subject": "Ripristino scomparsa dispositivo",       "execute": self.faultDeviceOff},

            # Esclusioni
            "BB": {"subject": "Esclusione zona",                        "execute": self.faultBypassOn},
            "BU": {"subject": "Ripristino esclusione",                  "execute": self.faultBypassOff},

            # Uscite
            "OU": {"subject": "Malfunzionamento uscita",                "execute": self.faultOutputOn},
            "OV": {"subject": "Ripristino malfunzionamento uscita",     "execute": self.faultOutputOff},

            # Solo informativi
            "BC": {"subject": "Reset memoria",                          "execute": self.onlyLastEvent},
            "JP": {"subject": "Riconoscimento codice/chiave",           "execute": self.onlyLastEvent},
            "DD": {"subject": "Codice/chiave errati",                   "execute": self.onlyLastEvent},
            "LB": {"subject": "Ingresso programmazione",                "execute": self.onlyLastEvent},
            "LX": {"subject": "Uscita programmazione",                  "execute": self.onlyLastEvent},
        }

    # -----------------------------------------------------------------------
    # Avvio: connette MQTT, carica config TCP, si iscrive al command topic
    # -----------------------------------------------------------------------

    def start(self, cfg: AppConfig):
        # Inizializza il componente TCP
        self._tcpExecutor = TcpCommandExecutor(cfg.alarm_ip, cfg.alarm_port)
        self._commands    = cfg.commands
        self._zones       = cfg.zones
        logging.info(f"Centralina INIM: {cfg.alarm_ip}:{cfg.alarm_port}, "
                     f"{len(self._commands)} comandi, {len(self._zones)} zone")

        # Costruisce e connette il client MQTT persistente
        self._mqttClient = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._mqttClient.username_pw_set(cfg.mqtt_user, cfg.mqtt_password)
        self._mqttClient.on_connect    = self._onConnect
        self._mqttClient.on_message    = self._onMqttMessage
        self._mqttClient.on_disconnect = self._onDisconnect

        logging.info(f"Connessione MQTT a {cfg.mqtt_broker}:{cfg.mqtt_port}")
        self._mqttClient.connect(cfg.mqtt_broker, cfg.mqtt_port, keepalive=60)

        self.mqttPublish(TOPIC_STARTUP, "started")
        # loop_start() avvia il thread di rete in background;
        # il chiamante (es. il server SIA-IP) gestirà il proprio loop principale.
        self._mqttClient.loop_start()

        threading.Thread(target=self._periodicCheck, daemon=True, name="periodic-check").start()

    # -----------------------------------------------------------------------
    # Callback MQTT
    # -----------------------------------------------------------------------

    def _onConnect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logging.info("MQTT connesso")
            client.subscribe(TOPIC_COMMAND)
            logging.info(f"Iscritto a {TOPIC_COMMAND}")
            # Sincronizza lo stato all'avvio
            self._execAlarmStatus()
        else:
            logging.error(f"MQTT connessione fallita, codice: {reason_code}")

    def _onDisconnect(self, client, userdata, flags, reason_code, properties):
        logging.warning(f"MQTT disconnesso (codice {reason_code}), riconnessione automatica...")

    def _onMqttMessage(self, client, userdata, msg):
        """Riceve i comandi da Home Assistant su alarm/command."""
        payload = msg.payload.decode('utf-8').strip()
        logging.info(f"MQTT ← {msg.topic} : {payload}")

        if payload == "ARM_AWAY":
            self._execArmAway()
        elif payload == "ARM_HOME":
            self._execArmHome()
        elif payload == "DISARM":
            self._execDisarm()
        else:
            logging.warning(f"Comando MQTT sconosciuto: {payload}")

    # -----------------------------------------------------------------------
    # Esecuzione comandi TCP → centralina
    # -----------------------------------------------------------------------

    def _runCommand(self, cmdName: str) -> tuple:
        """
        Cerca cmdName nei comandi caricati ed esegue i passi TCP.
        Ritorna (success, last_response).
        """
        if cmdName not in self._commands:
            logging.error(f"Comando non trovato nella configurazione: {cmdName}")
            return False, b""
        return self._tcpExecutor.run(self._commands[cmdName])

    def _execArmAway(self):
        """Verifica zone, poi inserisce totale."""
        logging.info("Comando: ARM_AWAY")
        ok, resp = self._runCommand("zone_verify")
        if not ok:
            logging.warning("ARM_AWAY annullato: verifica zone fallita")
            return
        if self._handleZoneErrors(resp):
            logging.warning("ARM_AWAY annullato: zone aperte")
            return
        ok, _ = self._runCommand("alarm_arm")
        if ok:
            logging.info("Inserimento totale riuscito")
            # lo stato verrà aggiornato dall'evento CL ricevuto via SIA-IP
        else:
            logging.error("Inserimento totale fallito")

    def _execArmHome(self):
        """Inserimento parziale (nessuna verifica zone per armed_home)."""
        logging.info("Comando: ARM_HOME")
        ok, _ = self._runCommand("alarm_arm_home")
        if ok:
            logging.info("Inserimento parziale riuscito")
        else:
            logging.error("Inserimento parziale fallito")

    def _execDisarm(self):
        logging.info("Comando: DISARM")
        ok, _ = self._runCommand("alarm_disarm")
        if ok:
            logging.info("Disinserimento riuscito")
            # lo stato verrà aggiornato dall'evento OP ricevuto via SIA-IP
        else:
            logging.error("Disinserimento fallito")

    def _execAlarmStatus(self):
        """Interroga la centralina, pubblica lo stato e restituisce la costante di stato (o None)."""
        logging.info("Richiesta stato centralina")
        ok, resp = self._runCommand("alarm_status")
        if not ok:
            logging.warning("Impossibile leggere lo stato della centralina")
            return None
        if resp == b"\x00\x00":
            self.mqttPublish(TOPIC_STATE, STATE_ARMED_AWAY)
            return STATE_ARMED_AWAY
        elif resp == b"\x01\x01":
            self.mqttPublish(TOPIC_STATE, STATE_ARMED_HOME)
            return STATE_ARMED_HOME
        elif resp == b"\x02\x02":
            self.mqttPublish(TOPIC_STATE, STATE_DISARMED)
            return STATE_DISARMED
        else:
            logging.warning(f"Risposta stato sconosciuta: {resp.hex()}")
            return None

    def _periodicCheck(self):
        """Ogni 10 minuti verifica stato; se disinserito, controlla anche le zone."""
        while True:
            time.sleep(600)
            logging.info("Verifica periodica stato centralina")
            status = self._execAlarmStatus()
            if status == STATE_DISARMED:
                ok, resp = self._runCommand("zone_verify")
                if ok:
                    self._handleZoneErrors(resp)
                else:
                    logging.warning("Verifica periodica zone: comando fallito")

    def _handleZoneErrors(self, response: bytes) -> bool:
        """Pubblica su alarm/errors le zone che risultano aperte."""
        messages = []
        for zone in self._zones:
            try:
                if response[zone.byte] & zone.value != 0:
                    messages.append(zone.description)
            except IndexError:
                pass
        payload = ", ".join(messages) if messages else "OK"
        self.mqttPublish(TOPIC_ATTRIBUTES, json.dumps({"errors": payload}))
        return bool(messages)

    # -----------------------------------------------------------------------
    # Parsing messaggi SIA-IP
    # -----------------------------------------------------------------------

    def manageAlarmMessage(self, msg):
        m = AlarmManager.alarmPattern.search(msg)
        if not m:
            return
        tipo  = m.group(1)
        param = m.group(2)
        desc  = re.sub(r'\s\s+', ' ', m.group(3)).strip()
        logging.info(f"SIA-IP evento: {tipo}, param: {param}, testo: {desc}")

        if tipo not in self.reactions:
            logging.warning(f"Evento SIA-IP sconosciuto: {tipo}: {desc}")
            return

        reaction      = self.reactions[tipo]
        subject       = reaction["subject"]
        message       = subject + (f": {desc}" if desc else "")
        executeMethod = reaction["execute"]

        self.mqttPublish(TOPIC_LAST_EVENT, message)

        if executeMethod:
            try:
                executeMethod(subject, message, param)
            except Exception as e:
                logging.error(f"Errore handler {tipo}: {e}")

    # -----------------------------------------------------------------------
    # Handler stato allarme (SIA-IP)
    # -----------------------------------------------------------------------

    def inserimentoTotale(self, subject, message, param):
        self.mqttPublish(TOPIC_STATE, STATE_ARMED_AWAY)

    def inserimentoParziale(self, subject, message, param):
        self.mqttPublish(TOPIC_STATE, STATE_ARMED_HOME)

    def disinserimento(self, subject, message, param):
        self.mqttPublish(TOPIC_STATE, STATE_DISARMED)

    def allarmeIntrusione(self, subject, message, param):
        self.mqttPublish(TOPIC_STATE, "triggered")

    def sabotaggio(self, subject, message, param):
        self.mqttPublish(TOPIC_STATE, "triggered")

    def ripristinoAllarme(self, subject, message, param):
        self._execAlarmStatus()

    # -----------------------------------------------------------------------
    # Handler fault binari (SIA-IP)
    # -----------------------------------------------------------------------

    def faultPowerOn(self, subject, message, param):   self.mqttPublish(TOPIC_POWER,   "ON")
    def faultPowerOff(self, subject, message, param):  self.mqttPublish(TOPIC_POWER,   "OFF")
    def faultBatteryOn(self, subject, message, param): self.mqttPublish(TOPIC_BATTERY, "ON")
    def faultBatteryOff(self, subject, message, param):self.mqttPublish(TOPIC_BATTERY, "OFF")
    def faultDeviceOn(self, subject, message, param):  self.mqttPublish(TOPIC_DEVICE,  "ON")
    def faultDeviceOff(self, subject, message, param): self.mqttPublish(TOPIC_DEVICE,  "OFF")
    def faultBypassOn(self, subject, message, param):  self.mqttPublish(TOPIC_BYPASS,  "ON")
    def faultBypassOff(self, subject, message, param): self.mqttPublish(TOPIC_BYPASS,  "OFF")
    def faultOutputOn(self, subject, message, param):  self.mqttPublish(TOPIC_OUTPUT,  "ON")
    def faultOutputOff(self, subject, message, param): self.mqttPublish(TOPIC_OUTPUT,  "OFF")

    def onlyLastEvent(self, subject, message, param):
        pass  # TOPIC_LAST_EVENT già pubblicato in manageAlarmMessage

    # -----------------------------------------------------------------------
    # MQTT publish (usa il client persistente)
    # -----------------------------------------------------------------------

    def mqttPublish(self, topic, payload):
        logging.info(f"MQTT → {topic} : {payload}")
        if self._mqttClient is None:
            logging.error("Client MQTT non inizializzato")
            return
        try:
            self._mqttClient.publish(topic, payload, retain=True)
        except Exception as e:
            logging.error(f"MQTT publish failed ({topic}): {e}")


# ===========================================================================
# Entry point (test / avvio standalone)
# ===========================================================================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.info('-------- AlarmManager startup --------')
    cfg = AppConfig.load()

    alarmManager = AlarmManager()
    alarmManager.start(cfg)

    # Test parsing SIA-IP
    s = '"SIA-DCS"0714L0#001234[#001234|Nri0OP4^Sirena Interna  ^]_11:47:38,11-13-2025'
    alarmManager.manageAlarmMessage(s)

    # Mantieni il processo in vita (in produzione ci pensa il server SIA-IP)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Interruzione manuale")
        alarmManager._mqttClient.loop_stop()
