#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
WhatsApp OTP Bot
Cardg API'den gelen OTP kodlarını WhatsApp üzerinden güvenli şekilde ileten bot
"""

import time
import threading
import tempfile
import uuid
import re
from datetime import datetime, timedelta
from flask import Flask, request, jsonify
import json
import os
import sys
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
import logging

# Logging konfigürasyonu
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('whatsapp_bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Flask uygulaması
app = Flask(__name__)

# OTP havuzu - {(telefon, tür): {"otp": kod, "timestamp": zaman}}
otp_pool = {}
otp_lock = threading.Lock()

# WhatsApp Web driver
driver = None
whatsapp_ready = False

class WhatsAppBot:
    def __init__(self):
        self.driver = None
        self.last_message_count = 0
        self.processed_messages = set()
        self.setup_driver()
        
    def setup_driver(self):
        """Chrome driver kurulumu"""
        chrome_options = Options()
        chrome_options.binary_location = "/bin/google-chrome"
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--disable-features=VizDisplayCompositor")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        
        try:
            self.driver = webdriver.Chrome(options=chrome_options)
            self.driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
            logger.info("Chrome driver başarıyla kuruldu")
        except Exception as e:
            logger.error(f"Chrome driver kurulumunda hata: {e}")
            raise
    
    def connect_whatsapp(self):
        """WhatsApp Web'e bağlanma"""
        try:
            logger.info("WhatsApp Web'e bağlanılıyor...")
            self.driver.get("https://web.whatsapp.com")
            
            # QR kod taranana kadar bekle
            logger.info("QR kodunu tarayın ve WhatsApp'a giriş yapın...")
            
            # Ana sayfanın yüklenmesini bekle - daha esnek selectors
            try:
                WebDriverWait(self.driver, 300).until(
                    EC.any_of(
                        EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='chat-list']")),
                        EC.presence_of_element_located((By.CSS_SELECTOR, "div[data-testid='chatlist-header']")),
                        EC.presence_of_element_located((By.CSS_SELECTOR, "div._2Ts6i")),
                        EC.presence_of_element_located((By.CSS_SELECTOR, ".two")),
                        EC.presence_of_element_located((By.XPATH, "//div[contains(text(), 'WhatsApp')]"))
                    )
                )
            except:
                # Manuel kontrol - sayfa yüklenmişse devam et
                time.sleep(5)
                logger.info("WhatsApp sayfası manuel olarak kontrol ediliyor...")
            
            logger.info("WhatsApp Web'e başarıyla bağlanıldı!")
            return True
            
        except Exception as e:
            logger.error(f"WhatsApp bağlantısında hata: {e}")
            return False
    
    def send_message(self, phone_number, message):
        """Belirtilen numaraya mesaj gönderme"""
        try:
            # Telefon numarasını temizle
            clean_phone = phone_number.replace("+", "").replace(" ", "")
            
            # Mesajı URL encode et
            import urllib.parse
            encoded_message = urllib.parse.quote(message)
            
            # WhatsApp direkt mesaj URL'si
            url = f"https://web.whatsapp.com/send?phone={clean_phone}&text={encoded_message}"
            self.driver.get(url)
            
            # Mesaj kutusunun yüklenmesini bekle
            WebDriverWait(self.driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "[data-testid='compose-btn-send']"))
            )
            
            # Gönder butonuna tıkla
            send_button = self.driver.find_element(By.CSS_SELECTOR, "[data-testid='compose-btn-send']")
            send_button.click()
            
            logger.info(f"Mesaj gönderildi: {phone_number}")
            time.sleep(3)
            
            # Ana sayfaya geri dön
            self.driver.get("https://web.whatsapp.com")
            time.sleep(2)
            
            return True
            
        except Exception as e:
            logger.error(f"Mesaj gönderiminde hata: {e}")
            return False
    
    def listen_messages(self):
        """WhatsApp mesajlarını dinleme - Sayfayı yenilemeden"""
        global whatsapp_ready
        whatsapp_ready = True
        logger.info("WhatsApp mesaj dinleme başlatıldı - Sayfayı yenilemeden dinleme modu")
        
        while True:
            try:
                # Sadece okunmamış sohbetleri kontrol et - sayfayı yenileme!
                unread_chats = self.driver.find_elements(By.CSS_SELECTOR, 
                    "div[data-testid='chat-list'] div[aria-label*='unread'], "
                    "div[data-testid='chat-list'] div[title*='unread'], "
                    "div[data-testid='chat-list'] span[data-testid='icon-unread-count']"
                )
                
                logger.info(f"🔍 {len(unread_chats)} okunmamış sohbet bulundu")
                
                for chat in unread_chats:
                    try:
                        # Sohbete tıkla
                        chat.click()
                        time.sleep(2)
                        
                        # Telefon numarasını al
                        phone = self.extract_phone_from_current_chat()
                        logger.info(f"📞 Sohbet telefonu: {phone}")
                        
                        # Yeni mesajları kontrol et
                        self.check_new_messages_in_chat(phone)
                        
                        # Ana listeye geri dön
                        back_button = self.driver.find_elements(By.CSS_SELECTOR, "[data-testid='back']")
                        if back_button:
                            back_button[0].click()
                            time.sleep(1)
                        
                    except Exception as e:
                        logger.error(f"Sohbet işlemede hata: {e}")
                        continue
                
                time.sleep(3)  # 3 saniye bekle
                
            except Exception as e:
                logger.error(f"Mesaj dinlemede ana hata: {e}")
                time.sleep(5)
    
    def check_new_messages_in_chat(self, phone):
        """Mevcut sohbetteki yeni mesajları kontrol et"""
        try:
            # Tüm mesaj konteynırlarını al
            message_containers = self.driver.find_elements(By.CSS_SELECTOR, 
                "div[data-testid='conversation-panel-messages'] div[data-testid='msg-container']"
            )
            
            # Son 5 mesajı kontrol et (sadece gelen mesajlar)
            for container in message_containers[-5:]:
                try:
                    # Gelen mesaj mı kontrol et (kendimizin gönderdiği değil)
                    is_outgoing = container.find_elements(By.CSS_SELECTOR, "div[data-testid='msg-meta'] span[data-testid='msg-dblcheck']")
                    if is_outgoing:  # Çift tik varsa bizim mesajımız, atla
                        continue
                    
                    # Mesaj metnini al
                    message_elements = container.find_elements(By.CSS_SELECTOR, 
                        "span.selectable-text, div.selectable-text"
                    )
                    
                    if message_elements:
                        message_text = message_elements[0].text.strip().lower()
                        
                        # Bu mesajı daha önce işledik mi?
                        msg_id = f"{phone}_{message_text}_{int(time.time() // 10)}"  # 10 saniye aralıklarla grupla
                        
                        if msg_id not in self.processed_messages:
                            self.processed_messages.add(msg_id)
                            
                            # Eski mesaj ID'lerini temizle (memory leak önleme)
                            if len(self.processed_messages) > 1000:
                                self.processed_messages = set(list(self.processed_messages)[-500:])
                            
                            logger.info(f"📨 Yeni mesaj: '{message_text}' - Telefon: {phone}")
                            
                            # Mesajı işle
                            self.process_message(phone, message_text)
                        
                except Exception as msg_error:
                    logger.error(f"Mesaj okuma hatası: {msg_error}")
                    continue
                    
        except Exception as e:
            logger.error(f"Sohbet mesajlarını kontrol etme hatası: {e}")
    
    def extract_phone_from_current_chat(self):
        """Mevcut sohbetten telefon numarasını çıkarma - İyileştirilmiş"""
        try:
            # Önce URL'den telefon numarasını almaya çalış
            current_url = self.driver.current_url
            if 'phone=' in current_url:
                phone_match = re.search(r'phone=(\d+)', current_url)
                if phone_match:
                    phone = '+' + phone_match.group(1)
                    logger.info(f"URL'den telefon numarası: {phone}")
                    return phone
            
            # Sohbet başlığından telefon numarasını al
            try:
                header_elements = self.driver.find_elements(By.CSS_SELECTOR, 
                    "[data-testid='conversation-header'] span, "
                    "[data-testid='conversation-header'] div"
                )
                
                for element in header_elements:
                    text = element.text.strip()
                    # Telefon numarası formatlarını kontrol et
                    clean_text = re.sub(r'[^\d+]', '', text)
                    if re.match(r'^\+?\d{10,15}$', clean_text):
                        if not clean_text.startswith('+'):
                            clean_text = '+' + clean_text
                        logger.info(f"Başlıktan telefon numarası: {clean_text}")
                        return clean_text
                        
            except Exception as header_error:
                logger.warning(f"Başlık okuma hatası: {header_error}")
            
            logger.warning("Telefon numarası bulunamadı")
            return None
            
        except Exception as e:
            logger.error(f"Telefon numarası çıkarma hatası: {e}")
            return None
    
    def process_message(self, phone, message):
        """Gelen mesajı işleme - İyileştirilmiş"""
        try:
            if not phone:
                logger.warning("Telefon numarası bulunamadığı için mesaj işlenemiyor")
                return
                
            message = message.strip().lower()
            logger.info(f"🔄 Mesaj işleniyor: '{message}' - Telefon: {phone}")
            
            # Anahtar kelimeleri kontrol et
            if any(keyword in message for keyword in ["oluşturma kodu", "düzenleme kodu", "oluşturma", "düzenleme"]):
                # Türü belirle
                tur = "oluşturma" if "oluşturma" in message else "düzenleme"
                
                logger.info(f"🎯 OTP talebi algılandı: {tur}")
                
                # OTP'yi bul ve gönder
                otp_code = self.get_otp_from_pool(phone, tur)
                
                if otp_code:
                    response_message = f"🔐 OTP Kodunuz: {otp_code}\n\nBu kod 5 dakika geçerlidir."
                    if self.send_message(phone, response_message):
                        logger.info(f"✅ OTP başarıyla gönderildi: {phone} - {tur} - {otp_code}")
                    else:
                        logger.error(f"❌ OTP gönderilemedi: {phone}")
                else:
                    error_message = "❌ Geçerli bir OTP kodu bulunamadı.\n\nLütfen önce işleminizi başlatın ve 5 dakika içinde kod talep edin."
                    self.send_message(phone, error_message)
                    logger.warning(f"⚠️ OTP bulunamadı: {phone} - {tur}")
            else:
                logger.info(f"📝 Bilinmeyen mesaj formatı: '{message}'")
                        
        except Exception as e:
            logger.error(f"Mesaj işlemede hata: {e}")
    
    def get_otp_from_pool(self, phone, tur):
        """OTP havuzundan kod alma - İyileştirilmiş telefon numarası eşleştirme"""
        with otp_lock:
            # Telefon numarasını normalize et
            normalized_phone = self.normalize_phone(phone)
            
            # Mümkün olan tüm telefon formatlarını dene
            possible_phones = [
                phone,
                normalized_phone,
                '+90' + phone.replace('+', '') if not phone.startswith('+90') else phone,
                '+355' + phone.replace('+', '') if not phone.startswith('+355') else phone
            ]
            
            # Benzersiz olanları al
            possible_phones = list(set(possible_phones))
            
            logger.info(f"🔍 OTP aranıyor. Denenen formatlar: {possible_phones}")
            logger.info(f"📋 Havuzdaki anahtarlar: {list(otp_pool.keys())}")
            
            for test_phone in possible_phones:
                key = (test_phone, tur)
                if key in otp_pool:
                    otp_data = otp_pool[key]
                    # Süre kontrolü
                    if datetime.now() - otp_data["timestamp"] < timedelta(minutes=5):
                        # OTP'yi kullan ve sil
                        otp_code = otp_data["otp"]
                        del otp_pool[key]
                        logger.info(f"✅ OTP bulundu ve silindi: {key}")
                        return otp_code
                    else:
                        # Süresi dolmuş, sil
                        del otp_pool[key]
                        logger.info(f"⏰ Süresi dolmuş OTP silindi: {key}")
            
            return None
    
    def normalize_phone(self, phone):
        """Telefon numarasını normalize et"""
        if not phone:
            return phone
            
        # Sadece rakamları al
        digits = re.sub(r'\D', '', phone)
        
        # Farklı formatları normalize et
        if digits.startswith('90') and len(digits) == 12:
            return '+' + digits
        elif digits.startswith('355') and len(digits) == 12:
            return '+' + digits
        elif digits.startswith('5') and len(digits) == 10:
            return '+90' + digits
        elif digits.startswith('0') and len(digits) == 11:
            return '+90' + digits[1:]
        else:
            return '+' + digits

# WhatsApp bot instance
whatsapp_bot = None

def cleanup_expired_otps():
    """Süresi dolmuş OTP'leri temizleme"""
    while True:
        try:
            current_time = datetime.now()
            with otp_lock:
                expired_keys = []
                for key, data in otp_pool.items():
                    if current_time - data["timestamp"] > timedelta(minutes=5):
                        expired_keys.append(key)
                
                for key in expired_keys:
                    del otp_pool[key]
                    logger.info(f"🗑️ Süresi dolmuş OTP temizlendi: {key}")
            
            time.sleep(60)  # Her dakika kontrol et
            
        except Exception as e:
            logger.error(f"OTP temizlemede hata: {e}")
            time.sleep(60)

@app.route('/otp', methods=['POST'])
def receive_otp():
    """Cardg API'den OTP alma endpoint'i"""
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "Geçersiz JSON"}), 400
        
        # Gerekli alanları kontrol et
        required_fields = ["tur", "otp", "tel"]
        for field in required_fields:
            if field not in data:
                return jsonify({"error": f"Eksik alan: {field}"}), 400
        
        tur = data["tur"]
        otp = data["otp"]
        tel = data["tel"]
        
        # Veri doğrulama
        if tur not in ["oluşturma", "düzenleme"]:
            return jsonify({"error": "Geçersiz tür"}), 400
        
        # Telefon numarası kontrolünü esnetiyoruz
        if not tel.startswith("+"):
            return jsonify({"error": "Telefon numarası + ile başlamalı"}), 400
        
        if not otp.isdigit() or len(otp) != 4:
            return jsonify({"error": "Geçersiz OTP formatı (4 haneli olmalı)"}), 400
        
        # OTP'yi havuza ekle
        with otp_lock:
            key = (tel, tur)
            otp_pool[key] = {
                "otp": otp,
                "timestamp": datetime.now()
            }
        
        logger.info(f"📥 OTP kaydedildi: {tel} - {tur} - {otp}")
        
        return jsonify({
            "message": "OTP başarıyla kaydedildi",
            "telefon": tel,
            "tur": tur,
            "timestamp": datetime.now().isoformat()
        }), 200
        
    except Exception as e:
        logger.error(f"OTP alma hatası: {e}")
        return jsonify({"error": "Sunucu hatası"}), 500

@app.route('/status', methods=['GET'])
def get_status():
    """Bot durumu kontrolü"""
    global whatsapp_ready
    
    return jsonify({
        "whatsapp_ready": whatsapp_ready,
        "active_otps": len(otp_pool),
        "timestamp": datetime.now().isoformat()
    })

@app.route('/pool', methods=['GET'])
def get_pool_status():
    """OTP havuzu durumu (debug için)"""
    with otp_lock:
        pool_info = {}
        for key, data in otp_pool.items():
            phone, tur = key
            pool_info[f"{phone}_{tur}"] = {
                "otp": data["otp"][:2] + "**",  # Güvenlik için kısmen gizle
                "age_minutes": (datetime.now() - data["timestamp"]).total_seconds() / 60
            }
    
    return jsonify({
        "pool_count": len(otp_pool),
        "pool_info": pool_info,
        "timestamp": datetime.now().isoformat()
    })

def main():
    """Ana fonksiyon"""
    global whatsapp_bot
    
    logger.info("🚀 WhatsApp OTP Bot başlatılıyor...")
    
    try:
        # WhatsApp bot'u başlat
        whatsapp_bot = WhatsAppBot()
        
        # WhatsApp'a bağlan
        if not whatsapp_bot.connect_whatsapp():
            logger.error("❌ WhatsApp bağlantısı kurulamadı!")
            return
        
        # OTP temizleme thread'ini başlat
        cleanup_thread = threading.Thread(target=cleanup_expired_otps, daemon=True)
        cleanup_thread.start()
        
        # WhatsApp mesaj dinleme thread'ini başlat
        listen_thread = threading.Thread(target=whatsapp_bot.listen_messages, daemon=True)
        listen_thread.start()
        
        logger.info("✅ Bot başarıyla başlatıldı!")
        logger.info("🌐 Flask server başlatılıyor...")
        
        # Flask server'ı başlat
        app.run(host='0.0.0.0', port=5000, debug=False)
        
    except KeyboardInterrupt:
        logger.info("🛑 Bot kapatılıyor...")
    except Exception as e:
        logger.error(f"❌ Bot başlatma hatası: {e}")
    finally:
        if whatsapp_bot and whatsapp_bot.driver:
            whatsapp_bot.driver.quit()

if __name__ == "__main__":
    main()
