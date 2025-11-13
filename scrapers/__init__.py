"""
Scrapers Module
Modüler scraper yapısı - Her kurum için ayrı scraper modülü
"""
from .sgk_kaysis_scraper import (
    scrape_sgk_mevzuat,
    print_results_to_console as print_sgk_results,
    get_uploaded_documents,
    check_if_document_exists,
    normalize_text,
    is_title_similar,
    turkish_title,
    turkish_sentence_case
)
from .adalet_bakanligi_scraper import (
    scrape_adalet_bakanligi_mevzuat,
    print_results_to_console as print_adalet_results
)
from .aile_sosyal_hizmetler_scraper import (
    scrape_aile_sosyal_hizmetler_mevzuat,
    print_results_to_console as print_aile_sosyal_results
)
from .calisma_sosyal_guvenlik_scraper import (
    scrape_calisma_sosyal_guvenlik_mevzuat,
    print_results_to_console as print_calisma_sosyal_results
)
from .cevre_sehircilik_iklim_scraper import (
    scrape_cevre_sehircilik_iklim_mevzuat,
    print_results_to_console as print_cevre_sehircilik_results
)
from .turkiye_is_kurumu_scraper import (
    scrape_turkiye_is_kurumu_mevzuat,
    print_results_to_console as print_turkiye_is_kurumu_results
)
from .adli_tip_kurumu_scraper import (
    scrape_adli_tip_kurumu_mevzuat,
    print_results_to_console as print_adli_tip_kurumu_results
)
from .ahiler_kalkinma_ajansi_scraper import (
    scrape_ahiler_kalkinma_ajansi_mevzuat,
    print_results_to_console as print_ahiler_kalkinma_results
)
from .afet_acil_durum_scraper import (
    scrape_afet_acil_durum_mevzuat,
    print_results_to_console as print_afet_acil_durum_results
)
from .ant_baskanligi_scraper import (
    scrape_ant_baskanligi_mevzuat,
    print_results_to_console as print_ant_baskanligi_results
)
from .anayasa_mahkemesi_scraper import (
    scrape_anayasa_mahkemesi_mevzuat,
    print_results_to_console as print_anayasa_mahkemesi_results
)
from .ankara_kalkinma_ajansi_scraper import (
    scrape_ankara_kalkinma_ajansi_mevzuat,
    print_results_to_console as print_ankara_kalkinma_results
)
from .ataturk_kultur_dil_tarih_scraper import (
    scrape_ataturk_kultur_dil_tarih_mevzuat,
    print_results_to_console as print_ataturk_kultur_dil_tarih_results
)
from .ataturk_orman_ciftligi_scraper import (
    scrape_ataturk_orman_ciftligi_mevzuat,
    print_results_to_console as print_ataturk_orman_ciftligi_results
)
from .avrupa_birligi_scraper import (
    scrape_avrupa_birligi_mevzuat,
    print_results_to_console as print_avrupa_birligi_results
)
from .avrupa_birligi_egitim_genclik_scraper import (
    scrape_avrupa_birligi_egitim_genclik_mevzuat,
    print_results_to_console as print_avrupa_birligi_egitim_genclik_results
)
from .bankacilik_duzenleme_denetleme_scraper import (
    scrape_bankacilik_duzenleme_denetleme_mevzuat,
    print_results_to_console as print_bankacilik_duzenleme_denetleme_results
)
from .basin_ilan_kurumu_scraper import (
    scrape_basin_ilan_kurumu_mevzuat,
    print_results_to_console as print_basin_ilan_kurumu_results
)
from .bati_akdeniz_kalkinma_ajansi_scraper import (
    scrape_bati_akdeniz_kalkinma_ajansi_mevzuat,
    print_results_to_console as print_bati_akdeniz_kalkinma_ajansi_results
)
from .bilgi_teknolojileri_iletisim_kurumu_scraper import (
    scrape_bilgi_teknolojileri_iletisim_kurumu_mevzuat,
    print_results_to_console as print_bilgi_teknolojileri_iletisim_kurumu_results
)
from .boru_hatlari_petrol_tasima_scraper import (
    scrape_boru_hatlari_petrol_tasima_mevzuat,
    print_results_to_console as print_boru_hatlari_petrol_tasima_results
)
from .bursa_eskisehir_bilecik_kalkinma_ajansi_scraper import (
    scrape_bursa_eskisehir_bilecik_kalkinma_ajansi_mevzuat,
    print_results_to_console as print_bursa_eskisehir_bilecik_kalkinma_ajansi_results
)
from .cumhurbaskanligi_scraper import (
    scrape_cumhurbaskanligi_mevzuat,
    print_results_to_console as print_cumhurbaskanligi_results
)
from .cumhurbaskanligi_yatirim_finans_ofisi_scraper import (
    scrape_cumhurbaskanligi_yatirim_finans_ofisi_mevzuat,
    print_results_to_console as print_cumhurbaskanligi_yatirim_finans_ofisi_results
)
from .calisma_sosyal_guvenlik_egitim_arastirma_merkezi_scraper import (
    scrape_calisma_sosyal_guvenlik_egitim_arastirma_merkezi_mevzuat,
    print_results_to_console as print_calisma_sosyal_guvenlik_egitim_arastirma_merkezi_results
)
from .canakkale_savaslari_gelibolu_tarihi_alan_baskanligi_scraper import (
    scrape_canakkale_savaslari_gelibolu_tarihi_alan_baskanligi_mevzuat,
    print_results_to_console as print_canakkale_savaslari_gelibolu_tarihi_alan_baskanligi_results
)
from .cay_isletmeleri_genel_mudurlugu_scraper import (
    scrape_cay_isletmeleri_genel_mudurlugu_mevzuat,
    print_results_to_console as print_cay_isletmeleri_genel_mudurlugu_results
)
from .cukurova_kalkinma_ajansi_scraper import (
    scrape_cukurova_kalkinma_ajansi_mevzuat,
    print_results_to_console as print_cukurova_kalkinma_ajansi_results
)

__all__ = [
    'scrape_sgk_mevzuat',
    'print_sgk_results',
    'scrape_adalet_bakanligi_mevzuat',
    'print_adalet_results',
    'scrape_aile_sosyal_hizmetler_mevzuat',
    'print_aile_sosyal_results',
    'scrape_calisma_sosyal_guvenlik_mevzuat',
    'print_calisma_sosyal_results',
    'scrape_cevre_sehircilik_iklim_mevzuat',
    'print_cevre_sehircilik_results',
    'scrape_turkiye_is_kurumu_mevzuat',
    'print_turkiye_is_kurumu_results',
    'scrape_adli_tip_kurumu_mevzuat',
    'print_adli_tip_kurumu_results',
    'scrape_ahiler_kalkinma_ajansi_mevzuat',
    'print_ahiler_kalkinma_results',
    'scrape_afet_acil_durum_mevzuat',
    'print_afet_acil_durum_results',
    'scrape_ant_baskanligi_mevzuat',
    'print_ant_baskanligi_results',
    'scrape_anayasa_mahkemesi_mevzuat',
    'print_anayasa_mahkemesi_results',
    'scrape_ankara_kalkinma_ajansi_mevzuat',
    'print_ankara_kalkinma_results',
    'scrape_ataturk_kultur_dil_tarih_mevzuat',
    'print_ataturk_kultur_dil_tarih_results',
    'scrape_ataturk_orman_ciftligi_mevzuat',
    'print_ataturk_orman_ciftligi_results',
    'scrape_avrupa_birligi_mevzuat',
    'print_avrupa_birligi_results',
    'scrape_avrupa_birligi_egitim_genclik_mevzuat',
    'print_avrupa_birligi_egitim_genclik_results',
    'scrape_bankacilik_duzenleme_denetleme_mevzuat',
    'print_bankacilik_duzenleme_denetleme_results',
    'scrape_basin_ilan_kurumu_mevzuat',
    'print_basin_ilan_kurumu_results',
    'scrape_bati_akdeniz_kalkinma_ajansi_mevzuat',
    'print_bati_akdeniz_kalkinma_ajansi_results',
    'scrape_bilgi_teknolojileri_iletisim_kurumu_mevzuat',
    'print_bilgi_teknolojileri_iletisim_kurumu_results',
    'scrape_boru_hatlari_petrol_tasima_mevzuat',
    'print_boru_hatlari_petrol_tasima_results',
    'scrape_bursa_eskisehir_bilecik_kalkinma_ajansi_mevzuat',
    'print_bursa_eskisehir_bilecik_kalkinma_ajansi_results',
    'scrape_cumhurbaskanligi_mevzuat',
    'print_cumhurbaskanligi_results',
    'scrape_cumhurbaskanligi_yatirim_finans_ofisi_mevzuat',
    'print_cumhurbaskanligi_yatirim_finans_ofisi_results',
    'scrape_calisma_sosyal_guvenlik_egitim_arastirma_merkezi_mevzuat',
    'print_calisma_sosyal_guvenlik_egitim_arastirma_merkezi_results',
    'scrape_canakkale_savaslari_gelibolu_tarihi_alan_baskanligi_mevzuat',
    'print_canakkale_savaslari_gelibolu_tarihi_alan_baskanligi_results',
    'scrape_cay_isletmeleri_genel_mudurlugu_mevzuat',
    'print_cay_isletmeleri_genel_mudurlugu_results',
    'scrape_cukurova_kalkinma_ajansi_mevzuat',
    'print_cukurova_kalkinma_ajansi_results',
    'get_uploaded_documents',
    'check_if_document_exists',
    'normalize_text',
    'is_title_similar',
    'turkish_title',
    'turkish_sentence_case'
]

