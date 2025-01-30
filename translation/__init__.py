from lingua import Language, LanguageDetectorBuilder
from deep_translator import GoogleTranslator


class LanguageDetection() : 
    def __init__(self ):
        languages = [Language.ENGLISH, Language.FRENCH, Language.GERMAN, Language.SPANISH , Language.SWEDISH , Language.DANISH , Language.FINNISH , Language.NYNORSK ]
        ### put norwegian 
        self.detector = LanguageDetectorBuilder.from_languages(*languages).build()        
        
    def detect_language(self, text) : 
        language = self.detector.detect_language_of(text)
        return language , language.name
    
    def translate_to_swedish(self, language , text) : 
        source_language = language.iso_code_639_1.name.lower()
        if source_language == 'nn' : 
            translated = GoogleTranslator(source= 'no' , target='sv').translate(text)
        else : 
            translated = GoogleTranslator(source= source_language , target='sv').translate(text)
        
        return translated
        
    