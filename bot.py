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
        self.last_chat_scan = 0
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
        """WhatsApp mesajlarını dinleme - YENİ YAKLAŞIM: Tüm sohbetleri tara"""
        global whatsapp_ready
        whatsapp_ready = True
        logger.info("🚀 WhatsApp mesaj dinleme başlatıldı - TÜM SOHBET TARAMA MODU")
        
        while True:
            try:
                # Ana sayfa kontrolü
                current_url = self.driver.current_url
                if 'web.whatsapp.com' not in current_url or 'send' in current_url:
                    logger.info("🔄 Ana sayfaya dönülüyor...")
                    self.driver.get("https://web.whatsapp.com")
                    time.sleep(3)
                
                # TÜM sohbetleri al (okunmuş/okunmamış fark etmez)
                all_chats = self.driver.find_elements(By.CSS_SELECTOR, 
                    "div[data-testid='chat-list'] div[data-testid='cell-frame-container']"
                )
                
                logger.info(f"🔍 Toplam {len(all_chats)} sohbet bulundu")
                
                # İlk 10 sohbeti kontrol et (son aktif olanlar)
                for i, chat in enumerate(all_chats[:10]):
                    try:
                        logger.info(f"📱 Sohbet {i+1} kontrol ediliyor...")
                        
                        # Sohbete tıkla
                        chat.click()
                        time.sleep(2)
                        
                        # Telefon numarasını al
                        phone = self.extract_phone_from_current_chat()
                        logger.info(f"📞 Telefon: {phone}")
                        
                        # Bu sohbetteki yeni mesajları kontrol et
                        if phone:
                            self.check_new_messages_in_chat(phone)
                        
                        # Ana listeye geri dön
                        self.driver.get("https://web.whatsapp.com")
                        time.sleep(1)
                        
                    except Exception as e:
                        logger.error(f"Sohbet {i+1} işlemede hata: {e}")
                        # Ana sayfaya dön
                        self.driver.get("https://web.whatsapp.com")
                        time.sleep(1)
                        continue
                
                logger.info("💤 5 saniye bekleniyor...")
                time.sleep(5)
                
            except Exception as e:
                logger.error(f"Ana mesaj dinleme hatası: {e}")
                time.sleep(10)
    
    def check_new_messages_in_chat(self, phone):
        """Mevcut sohbetteki yeni mesajları kontrol et - İYİLEŞTİRİLMİŞ"""
        try:
            # Sayfanın yüklenmesini bekle
            time.sleep(2)
            
            # Tüm mesajları al (farklı selector'lar dene)
            message_containers = []
            
            # Farklı selector'ları dene
            selectors = [
                "div[data-testid='conversation-panel-messages'] div[data-testid='msg-container']",
                "div[data-testid='msg-container']", 
                ".message-in, .message-out"
            ]
            
            for selector in selectors:
                containers = self.driver.find_elements(By.CSS_SELECTOR, selector)
                if containers:
                    message_containers = containers
                    logger.info(f"✅ {len(containers)} mesaj bulundu (selector: {selector})")
                    break
            
            if not message_containers:
                logger.warning("❌ Hiç mesaj bulunamadı")
                return
            
            # Son 3 mesajı kontrol et
            recent_messages = message_containers[-3:]
            logger.info(f"🔍 Son {len(recent_messages)} mesaj kontrol ediliyor...")
            
            for i, container in enumerate(recent_messages):
                try:
                    # Giden mesaj mı kontrol et (çift tik var mı?)
                    outgoing_indicators = container.find_elements(By.CSS_SELECTOR, 
                        "span[data-testid='msg-dblcheck'], span[data-testid='msg-check']"
                    )
                    
                    if outgoing_indicators:
                        logger.info(f"⬆️ Mesaj {i+1}: Giden mesaj (atlanıyor)")
                        continue
                    
                    # Mesaj metnini al - farklı yolları dene
                    message_text = None
                    text_selectors = [
                        "span.selectable-text",
                        "div.selectable-text", 
                        "span._ao3e",
                        "div._ao3e"
                    ]
                    
                    for text_selector in text_selectors:
                        text_elements = container.find_elements(By.CSS_SELECTOR, text_selector)
                        if text_elements and text_elements[0].text.strip():
                            message_text = text_elements[0].text.strip().lower()
                            break
                    
                    if not message_text:
                        logger.info(f"📝 Mesaj {i+1}: Metin bulunamadı")
                        continue
                    
                    logger.info(f"📨 Mesaj {i+1}: '{message_text}'")
                    
                    # Bu mesajı daha önce işledik mi?
                    current_time = int(time.time())
                    msg_id = f"{phone}_{message_text}_{current_time // 30}"  # 30 saniye grupları
                    
                    if msg_id not in self.processed_messages:
                        self.processed_messages.add(msg_id)
                        
                        # Memory temizliği
                        if len(self.processed_messages) > 500:
                            self.processed_messages = set(list(self.processed_messages)[-250:])
                        
                        logger.info(f"🆕 YENİ MESAJ ALGILANDI: '{message_text}' - {phone}")
                        
                        # Mesajı işle
                        self.process_message(phone, message_text)
                    else:
                        logger.info(f"🔄 Mesaj daha önce işlendi: {msg_id}")
                        
                except Exception as msg_error:
                    logger.error(f"Mesaj {i+1} okuma hatası: {msg_error}")
                    continue
                    
        except Exception as e:
            logger.error(f"Sohbet mesajları kontrol hatası: {e}")
    
    def extract_phone_from_current_chat(self):
        """Mevcut sohbetten telefon numarasını çıkarma - SUPER İYİLEŞTİRİLMİŞ"""
        try:
            # 1. URL'den telefon numarasını al (en güvenilir)
            current_url = self.driver.current_url
            if 'phone=' in current_url:
                phone_match = re.search(r'phone=(\d+)', current_url)
                if phone_match:
                    phone = '+' + phone_match.group(1)
                    logger.info(f"🎯 URL'den telefon: {phone}")
                    return phone
            
            # 2. Sayfa başlığından al
            try:
                title_elements = self.driver.find_elements(By.CSS_SELECTOR, 
                    "[data-testid='conversation-header'] span, "
                    "header span, "
                    "h1, h2, h3"
                )
                
                for element in title_elements:
                    text = element.text.strip()
                    # Telefon formatını kontrol et
                    if re.search(r'\+?\d{10,15}', text):
                        clean_number = re.sub(r'[^\d+]', '', text)
                        if len(clean_number) >= 10:
                            if not clean_number.startswith('+'):
                                clean_number = '+' + clean_number
                            logger.info(f"📋 Başlıktan telefon: {clean_number}")
                            return clean_number
                            
            except Exception as header_error:
                logger.warning(f"Başlık okuma hatası: {header_error}")
            
            # 3. Sayfadaki tüm telefon benzeri metinleri tara
            try:
                all_elements = self.driver.find_elements(By.CSS_SELECTOR, "span, div")
                for element in all_elements:
                    text = element.text.strip()
                    if text and re.match(r'^\+\d{10,15}$', text.replace(' ', '')):
                        logger.info(f"🔍 Genel taramadan telefon: {text}")
                        return text.replace(' ', '')
                        
            except Exception as scan_error:
                logger.warning(f"Genel tarama hatası: {scan_error}")
            
            logger.warning("❌ Telefon numarası bulunamadı")
            return None
            
        except Exception as e:
            logger.error(f"Telefon çıkarma ana hatası: {e}")
            return None
    
    def process_message(self, phone, message):
        """Gelen mesajı işleme - ULTRA İYİLEŞTİRİLMİŞ"""
        try:
            if not phone:
                logger.warning("⚠️ Telefon numarası yok, mesaj işlenemiyor")
                return
                
            message = message.strip().lower()
            logger.info(f"🔄 MESAJ İŞLENİYOR: '{message}' - Telefon: {phone}")
            
            # Anahtar kelime kontrolü - daha esnek
            keywords = ["oluşturma kodu", "düzenleme kodu", "oluşturma", "düzenleme", "kod", "otp"]
            
            if any(keyword in message for keyword in keywords):
                # Türü belirle
                if "oluşturma" in message:
                    tur = "oluşturma"
                elif "düzenleme" in message:
                    tur = "düzenleme"
                else:
                    # Default olarak oluşturma
                    tur = "oluşturma"
                
                logger.info(f"🎯 OTP TALEBİ ALGILANDI: '{tur}' - {phone}")
                
                # OTP'yi bul ve gönder
                otp_code = self.get_otp_from_pool(phone, tur)
                
                if otp_code:
                    response_message = f"🔐 OTP Kodunuz: {otp_code}\n\nBu kod 5 dakika geçerlidir."
                    if self.send_message(phone, response_message):
                        logger.info(f"✅ OTP BAŞARIYLA GÖNDERİLDİ: {phone} - {tur} - {otp_code}")
                    else:
                        logger.error(f"❌ OTP GÖNDERİLEMEDİ: {phone}")
                else:
                    error_message = "❌ Geçerli bir OTP kodu bulunamadı.\n\nLütfen önce işleminizi başlatın ve 5 dakika içinde kod talep edin."
                    self.send_message(phone, error_message)
                    logger.warning(f"⚠️ OTP BULUNAMADI: {phone} - {tur}")
            else:
                logger.info(f"📝 Bilinmeyen mesaj: '{message}'")
                        
        except Exception as e:
            logger.error(f"Mesaj işleme hatası: {e}")
    
    def get_otp_from_pool(self, phone, tur):
        """OTP havuzundan kod alma - SÜPER İYİLEŞTİRİLMİŞ"""
        with otp_lock:
            # Telefon numarası formatlarını normalize et
            possible_phones = self.generate_phone_variants(phone)
            
            logger.info(f"🔍 OTP ARANACAK FORMATLAR: {possible_phones}")
            logger.info(f"📋 HAVUZDAKI ANAHTARLAR: {list(otp_pool.keys())}")
            
            for test_phone in possible_phones:
                key = (test_phone, tur)
                if key in otp_pool:
                    otp_data = otp_pool[key]
                    # Süre kontrolü
                    if datetime.now() - otp_data["timestamp"] < timedelta(minutes=5):
                        # OTP'yi kullan ve sil
                        otp_code = otp_data["otp"]
                        del otp_pool[key]
                        logger.info(f"✅ OTP BULUNDU VE SİLİNDİ: {key}")
                        return otp_code
                    else:
                        # Süresi dolmuş, sil
                        del otp_pool[key]
                        logger.info(f"⏰ SÜRESİ DOLMUŞ OTP SİLİNDİ: {key}")
            
            logger.warning(f"❌ OTP BULUNAMADI - Telefon: {phone}, Tür: {tur}")
            return None
    
    def generate_phone_variants(self, phone):
        """Telefon numarası varyantları üret"""
        if not phone:
            return []
            
        variants = set()
        
        # Orijinal
        variants.add(phone)
        
        # Sadece rakamlar
        digits = re.sub(r'\D', '', phone)
        
        # Farklı formatlar
        if digits:
            variants.add('+' + digits)
            variants.add(digits)
            
            # Türkiye formatları
            if len(digits) == 10 and digits.startswith('5'):
                variants.add('+90' + digits)
            elif len(digits) == 11 and digits.startswith('05'):
                variants.add('+90' + digits[1:])
            elif len(digits) == 12 and digits.startswith('90'):
                variants.add('+' + digits)
            
            # Arnavutluk formatları  
            if len(digits) == 9 and digits.startswith('6'):
                variants.add('+355' + digits)
            elif len(digits) == 12 and digits.startswith('355'):
                variants.add('+' + digits)
        
        return list(variants)
    
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
        
        logger.info(f"📥 OTP KAYDEDİLDİ: {tel} - {tur} - {otp}")
        
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
