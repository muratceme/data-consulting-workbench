"""e-Belge blokları: UBL-TR e-İrsaliye üretimi ve e-Fatura/e-İrsaliye için birim kodu eşleme. Birim kodları GİB UBL-TR kod listesindeki UN/ECE
Recommendation 20 kodlarıdır. Üretilen XML’ler imzasızdır; GİB’e bağlanılmaz."""
import re, xml.etree.ElementTree as ET
from collections import defaultdict
from xml.sax.saxutils import escape
import tabular
import turkiye as tr
from islem import op, arg, Result, need

REQUIRES = ['turkiye']
# GİB UBL-TR kod listesi (UBL-TR_Codelist.xml, UnitCodeList).
GIB_UNITS = frozenset((
    '05', '06', '08', '10', '11', '13', '14', '15', '16', '17', '18', '19', '1A', '1B', '1C', '1D', '1E', '1F', '1G', '1H', '1I', '1J', '1K', '1L',
    '1M', '1X', '20', '21', '22', '23', '24', '25', '26', '27', '28', '29', '2A', '2B', '2C', '2G', '2H', '2I', '2J', '2K', '2L', '2M', '2N', '2P',
    '2Q', '2R', '2U', '2V', '2W', '2X', '2Y', '2Z', '30', '31', '32', '33', '34', '35', '36', '37', '38', '3B', '3C', '3E', '3G', '3H', '3I', '40',
    '41', '43', '44', '45', '46', '47', '48', '4A', '4B', '4C', '4E', '4G', '4H', '4K', '4L', '4M', '4N', '4O', '4P', '4Q', '4R', '4T', '4U', '4W',
    '4X', '53', '54', '56', '57', '58', '59', '5A', '5B', '5C', '5E', '5F', '5G', '5H', '5I', '5J', '5K', '5P', '5Q', '60', '61', '62', '63', '64',
    '66', '69', '71', '72', '73', '74', '76', '77', '78', '80', '81', '84', '85', '87', '89', '90', '91', '92', '93', '94', '95', '96', '97', '98',
    'A1', 'A10', 'A11', 'A12', 'A13', 'A14', 'A15', 'A16', 'A17', 'A18', 'A19', 'A2', 'A20', 'A21', 'A22', 'A23', 'A24', 'A25', 'A26', 'A27', 'A28',
    'A29', 'A3', 'A30', 'A31', 'A32', 'A33', 'A34', 'A35', 'A36', 'A37', 'A38', 'A39', 'A4', 'A40', 'A41', 'A42', 'A43', 'A44', 'A45', 'A47', 'A48',
    'A49', 'A5', 'A50', 'A51', 'A52', 'A53', 'A54', 'A55', 'A56', 'A57', 'A58', 'A59', 'A6', 'A60', 'A61', 'A62', 'A63', 'A64', 'A65', 'A66', 'A67',
    'A68', 'A69', 'A7', 'A70', 'A71', 'A73', 'A74', 'A75', 'A76', 'A77', 'A78', 'A79', 'A8', 'A80', 'A81', 'A82', 'A83', 'A84', 'A85', 'A86', 'A87',
    'A88', 'A89', 'A9', 'A90', 'A91', 'A93', 'A94', 'A95', 'A96', 'A97', 'A98', 'A99', 'AA', 'AB', 'ACR', 'ACT', 'AD', 'AE', 'AH', 'AI', 'AJ', 'AK',
    'AL', 'AM', 'AMH', 'AMP', 'AMVT', 'ANN', 'AP', 'AO', 'APZ', 'AQ', 'AR', 'ARE', 'AS', 'ASM', 'ASU', 'ATM', 'ATT', 'AV', 'AW', 'AWG', 'AY', 'AYR',
    'AZ', 'B0', 'B1', 'B10', 'B11', 'B12', 'B13', 'B14', 'B15', 'B16', 'B17', 'B18', 'B19', 'B2', 'B20', 'B21', 'B22', 'B23', 'B24', 'B25', 'B26',
    'B27', 'B28', 'B29', 'B3', 'B30', 'B31', 'B32', 'B33', 'B34', 'B35', 'B36', 'B37', 'B38', 'B39', 'B4', 'B40', 'B41', 'B42', 'B43', 'B44', 'B45',
    'B46', 'B47', 'B48', 'B49', 'B5', 'B50', 'B51', 'B52', 'B53', 'B54', 'B55', 'B56', 'B57', 'B58', 'B59', 'B6', 'B60', 'B61', 'B62', 'B63', 'B64',
    'B65', 'B66', 'B67', 'B68', 'B69', 'B7', 'B70', 'B71', 'B72', 'B73', 'B74', 'B75', 'B76', 'B77', 'B78', 'B79', 'B8', 'B80', 'B81', 'B82', 'B83',
    'B84', 'B85', 'B86', 'B87', 'B88', 'B89', 'B9', 'B90', 'B91', 'B92', 'B93', 'B94', 'B95', 'B96', 'B97', 'B98', 'B99', 'BAR', 'BAS', 'BB', 'BD',
    'BE', 'BFT', 'BG', 'BH', 'BHP', 'BIL', 'BJ', 'BK', 'BL', 'BLD', 'BLL', 'BO', 'BP', 'BPM', 'BQL', 'BR', 'BT', 'BTU', 'BUA', 'BUI', 'BW', 'BX',
    'BZ', 'C0', 'C1', 'C10', 'C11', 'C12', 'C13', 'C14', 'C15', 'C16', 'C17', 'C18', 'C19', 'C2', 'C20', 'C21', 'C22', 'C23', 'C24', 'C25', 'C26',
    'C27', 'C28', 'C29', 'C3', 'C30', 'C31', 'C32', 'C33', 'C34', 'C35', 'C36', 'C37', 'C38', 'C39', 'C4', 'C40', 'C41', 'C42', 'C43', 'C44', 'C45',
    'C46', 'C47', 'C48', 'C49', 'C5', 'C50', 'C51', 'C52', 'C53', 'C54', 'C55', 'C56', 'C57', 'C58', 'C59', 'C6', 'C60', 'C61', 'C62', 'C63', 'C64',
    'C65', 'C66', 'C67', 'C68', 'C69', 'C7', 'C70', 'C71', 'C72', 'C73', 'C74', 'C75', 'C76', 'C77', 'C78', 'C79', 'C8', 'C80', 'C81', 'C82', 'C83',
    'C84', 'C85', 'C86', 'C87', 'C88', 'C89', 'C9', 'C90', 'C91', 'C92', 'C93', 'C94', 'C95', 'C96', 'C97', 'C98', 'C99', 'CA', 'CCT', 'CDL', 'CEL',
    'CEN', 'CG', 'CGM', 'CH', 'CJ', 'CK', 'CKG', 'CL', 'CLF', 'CLT', 'CMK', 'CMQ', 'CMT', 'CNP', 'CNT', 'CO', 'COU', 'CPR', 'CQ', 'CR', 'CS', 'CT',
    'CTG', 'CTM', 'CTN', 'CU', 'CUR', 'CV', 'CWA', 'CWI', 'CY', 'CZ', 'D03', 'D04', 'D1', 'D10', 'D11', 'D12', 'D13', 'D14', 'D15', 'D16', 'D17',
    'D18', 'D19', 'D2', 'D20', 'D21', 'D22', 'D23', 'D24', 'D25', 'D26', 'D27', 'D28', 'D29', 'D30', 'D31', 'D32', 'D33', 'D34', 'D35', 'D36', 'D37',
    'D38', 'D39', 'D40', 'D41', 'D42', 'D43', 'D44', 'D45', 'D46', 'D47', 'D48', 'D49', 'D5', 'D50', 'D51', 'D52', 'D53', 'D54', 'D55', 'D56', 'D57',
    'D58', 'D59', 'D6', 'D60', 'D61', 'D62', 'D63', 'D64', 'D65', 'D66', 'D67', 'D68', 'D69', 'D7', 'D70', 'D71', 'D72', 'D73', 'D74', 'D75', 'D76',
    'D77', 'D78', 'D79', 'D8', 'D80', 'D81', 'D82', 'D83', 'D85', 'D86', 'D87', 'D88', 'D89', 'D9', 'D90', 'D91', 'D92', 'D93', 'D94', 'D95', 'D96',
    'D97', 'D98', 'D99', 'DAA', 'DAD', 'DAY', 'DB', 'DBM', 'DBW', 'DC', 'DD', 'DE', 'DEC', 'DG', 'DI', 'DJ', 'DLT', 'DMA', 'DMK', 'DMO', 'DMQ',
    'DMT', 'DN', 'DPC', 'DPR', 'DPT', 'DQ', 'DR', 'DRA', 'DRI', 'DRL', 'DRM', 'DS', 'DT', 'DTN', 'DU', 'DWT', 'DX', 'DY', 'DZN', 'DZP', 'E01', 'E07',
    'E08', 'E09', 'E10', 'E11', 'E12', 'E14', 'E15', 'E16', 'E17', 'E18', 'E19', 'E2', 'E20', 'E21', 'E22', 'E23', 'E25', 'E27', 'E28', 'E3', 'E30',
    'E31', 'E32', 'E33', 'E34', 'E35', 'E36', 'E37', 'E38', 'E39', 'E4', 'E40', 'E41', 'E42', 'E43', 'E44', 'E45', 'E46', 'E47', 'E48', 'E49', 'E5',
    'E50', 'E51', 'E52', 'E53', 'E54', 'E55', 'E56', 'E57', 'E58', 'E59', 'E60', 'E61', 'E62', 'E63', 'E64', 'E65', 'E66', 'E67', 'E68', 'E69',
    'E70', 'E71', 'E72', 'E73', 'E74', 'E75', 'E76', 'E77', 'E78', 'E79', 'E80', 'E81', 'E82', 'E83', 'E84', 'E85', 'E86', 'E87', 'E88', 'E89',
    'E90', 'E91', 'E92', 'E93', 'E94', 'E95', 'E96', 'E97', 'E98', 'E99', 'EA', 'EB', 'EC', 'EP', 'EQ', 'EV', 'F01', 'F02', 'F03', 'F04', 'F05',
    'F06', 'F07', 'F08', 'F1', 'F10', 'F11', 'F12', 'F13', 'F14', 'F15', 'F16', 'F17', 'F18', 'F19', 'F20', 'F21', 'F22', 'F23', 'F24', 'F25', 'F26',
    'F27', 'F28', 'F29', 'F30', 'F31', 'F32', 'F33', 'F34', 'F35', 'F36', 'F37', 'F38', 'F39', 'F40', 'F41', 'F42', 'F43', 'F44', 'F45', 'F46',
    'F47', 'F48', 'F49', 'F50', 'F51', 'F52', 'F53', 'F54', 'F55', 'F56', 'F57', 'F58', 'F59', 'F60', 'F61', 'F62', 'F63', 'F64', 'F65', 'F66',
    'F67', 'F68', 'F69', 'F70', 'F71', 'F72', 'F73', 'F74', 'F75', 'F76', 'F77', 'F78', 'F79', 'F80', 'F81', 'F82', 'F83', 'F84', 'F85', 'F86',
    'F87', 'F88', 'F89', 'F9', 'F90', 'F91', 'F92', 'F93', 'F94', 'F95', 'F96', 'F97', 'F98', 'F99', 'FAH', 'FAR', 'FB', 'FBM', 'FC', 'FD', 'FE',
    'FF', 'FG', 'FH', 'FIT', 'FL', 'FM', 'FNU', 'FOT', 'FP', 'FR', 'FS', 'FTK', 'FTQ', 'G01', 'G04', 'G05', 'G06', 'G08', 'G09', 'G10', 'G11', 'G12',
    'G13', 'G14', 'G15', 'G16', 'G17', 'G18', 'G19', 'G2', 'G20', 'G21', 'G23', 'G24', 'G25', 'G26', 'G27', 'G28', 'G29', 'G3', 'G30', 'G31', 'G32',
    'G33', 'G34', 'G35', 'G36', 'G37', 'G38', 'G39', 'G40', 'G41', 'G42', 'G43', 'G44', 'G45', 'G46', 'G47', 'G48', 'G49', 'G50', 'G51', 'G52',
    'G53', 'G54', 'G55', 'G56', 'G57', 'G58', 'G59', 'G60', 'G61', 'G62', 'G63', 'G64', 'G65', 'G66', 'G67', 'G68', 'G69', 'G7', 'G70', 'G71', 'G72',
    'G73', 'G74', 'G75', 'G76', 'G77', 'G78', 'G79', 'G80', 'G81', 'G82', 'G83', 'G84', 'G85', 'G86', 'G87', 'G88', 'G89', 'G90', 'G91', 'G92',
    'G93', 'G94', 'G95', 'G96', 'G97', 'G98', 'G99', 'GB', 'GBQ', 'GC', 'GD', 'GDW', 'GE', 'GF', 'GFI', 'GGR', 'GH', 'GIA', 'GIC', 'GII', 'GIP',
    'GJ', 'GK', 'GL', 'GLD', 'GLI', 'GLL', 'GM', 'GMS', 'GN', 'GO', 'GP', 'GQ', 'GRM', 'GRN', 'GRO', 'GRT', 'GT', 'GV', 'GW', 'GWH', 'GY', 'GZ',
    'H03', 'H04', 'H05', 'H06', 'H07', 'H08', 'H09', 'H1', 'H10', 'H11', 'H12', 'H13', 'H14', 'H15', 'H16', 'H18', 'H19', 'H2', 'H20', 'H21', 'H22',
    'H23', 'H24', 'H25', 'H26', 'H27', 'H28', 'H29', 'H30', 'H31', 'H32', 'H33', 'H34', 'H35', 'H36', 'H37', 'H38', 'H39', 'H40', 'H41', 'H42',
    'H43', 'H44', 'H45', 'H46', 'H47', 'H48', 'H49', 'H50', 'H51', 'H52', 'H53', 'H54', 'H55', 'H56', 'H57', 'H58', 'H59', 'H60', 'H61', 'H62',
    'H63', 'H64', 'H65', 'H66', 'H67', 'H68', 'H69', 'H70', 'H71', 'H72', 'H73', 'H74', 'H75', 'H76', 'H77', 'H78', 'H79', 'H80', 'H81', 'H82',
    'H83', 'H84', 'H85', 'H87', 'H88', 'H89', 'H90', 'H91', 'H92', 'H93', 'H94', 'H95', 'H96', 'H98', 'H99', 'HA', 'HAD', 'HAGR', 'HAR', 'HBA',
    'HBX', 'HC', 'HD', 'HDW', 'HE', 'HEA', 'HF', 'HGM', 'HH', 'HI', 'HIU', 'HJ', 'HK', 'HKM', 'HL', 'HLT', 'HM', 'HMO', 'HMQ', 'HMT', 'HN', 'HO',
    'HP', 'HPA', 'HS', 'HT', 'HTZ', 'HUR', 'HWE', 'HY', 'IA', 'IC', 'IE', 'IF', 'II', 'IL', 'IM', 'INH', 'INK', 'INQ', 'IP', 'ISD', 'IT', 'IU',
    'IUG', 'IV', 'J10', 'J12', 'J13', 'J14', 'J15', 'J16', 'J17', 'J18', 'J19', 'J2', 'J20', 'J21', 'J22', 'J23', 'J24', 'J25', 'J26', 'J27', 'J28',
    'J29', 'J30', 'J31', 'J32', 'J33', 'J34', 'J35', 'J36', 'J38', 'J39', 'J40', 'J41', 'J42', 'J43', 'J44', 'J45', 'J46', 'J47', 'J48', 'J49',
    'J50', 'J51', 'J52', 'J53', 'J54', 'J55', 'J56', 'J57', 'J58', 'J59', 'J60', 'J61', 'J62', 'J63', 'J64', 'J65', 'J66', 'J67', 'J68', 'J69',
    'J70', 'J71', 'J72', 'J73', 'J74', 'J75', 'J76', 'J78', 'J79', 'J81', 'J82', 'J83', 'J84', 'J85', 'J87', 'J89', 'J90', 'J91', 'J92', 'J93',
    'J94', 'J95', 'J96', 'J97', 'J98', 'J99', 'JB', 'JE', 'JG', 'JK', 'JM', 'JNT', 'JO', 'JOU', 'JPS', 'JR', 'JWL', 'K1', 'K10', 'K11', 'K12', 'K13',
    'K14', 'K15', 'K16', 'K17', 'K18', 'K19', 'K2', 'K20', 'K21', 'K22', 'K23', 'K24', 'K25', 'K26', 'K27', 'K28', 'K3', 'K30', 'K31', 'K32', 'K33',
    'K34', 'K35', 'K36', 'K37', 'K38', 'K39', 'K40', 'K41', 'K42', 'K43', 'K45', 'K46', 'K47', 'K48', 'K49', 'K5', 'K50', 'K51', 'K52', 'K53', 'K54',
    'K55', 'K58', 'K59', 'K6', 'K60', 'K61', 'K62', 'K63', 'K64', 'K65', 'K66', 'K67', 'K68', 'K69', 'K70', 'K71', 'K73', 'K74', 'K75', 'K76', 'K77',
    'K78', 'K79', 'K80', 'K81', 'K82', 'K83', 'K84', 'K85', 'K86', 'K87', 'K88', 'K89', 'K90', 'K91', 'K92', 'K93', 'K94', 'K95', 'K96', 'K97',
    'K98', 'K99', 'KA', 'KAT', 'KB', 'KBA', 'KCC', 'KD', 'KDW', 'KEL', 'KF', 'KFO', 'KG', 'KGM', 'KGS', 'KH6', 'KHO', 'KHY', 'KHZ', 'KI', 'KIC',
    'KIP', 'KJ', 'KJO', 'KL', 'KLK', 'KLX', 'KMA', 'KMH', 'KMK', 'KMQ', 'KMT', 'KNI', 'KNM', 'KNS', 'KNT', 'KO', 'KOH', 'KPA', 'KPH', 'KPO', 'KPP',
    'KPR', 'KR', 'KS', 'KSD', 'KSH', 'KT', 'KTM', 'KTN', 'KUR', 'KVA', 'KVR', 'KVT', 'KW', 'KWH', 'KWN', 'KWO', 'KWS', 'KWT', 'KWY', 'KX', 'L10',
    'L11', 'L12', 'L13', 'L14', 'L15', 'L16', 'L17', 'L18', 'L19', 'L2', 'L20', 'L21', 'L23', 'L24', 'L25', 'L26', 'L27', 'L28', 'L29', 'L30', 'L31',
    'L32', 'L33', 'L34', 'L35', 'L36', 'L37', 'L38', 'L39', 'L40', 'L41', 'L42', 'L43', 'L44', 'L45', 'L46', 'L47', 'L48', 'L49', 'L50', 'L51',
    'L52', 'L53', 'L54', 'L55', 'L56', 'L57', 'L58', 'L59', 'L60', 'L61', 'L62', 'L63', 'L64', 'L65', 'L66', 'L67', 'L68', 'L69', 'L70', 'L71',
    'L72', 'L73', 'L74', 'L75', 'L76', 'L77', 'L78', 'L79', 'L80', 'L81', 'L82', 'L83', 'L84', 'L85', 'L86', 'L87', 'L88', 'L89', 'L90', 'L91',
    'L92', 'L93', 'L94', 'L95', 'L96', 'L98', 'L99', 'LA', 'LAC', 'LBR', 'LBT', 'LC', 'LD', 'LE', 'LEF', 'LF', 'LH', 'LI', 'LJ', 'LK', 'LM', 'LN',
    'LO', 'LP', 'LPA', 'LR', 'LS', 'LTN', 'LTR', 'LUB', 'LUM', 'LUX', 'LX', 'LY', 'M0', 'M1', 'M10', 'M11', 'M12', 'M13', 'M14', 'M15', 'M16', 'M17',
    'M18', 'M19', 'M20', 'M21', 'M22', 'M23', 'M24', 'M25', 'M26', 'M27', 'M29', 'M30', 'M31', 'M32', 'M33', 'M34', 'M35', 'M36', 'M37', 'M38',
    'M39', 'M4', 'M40', 'M41', 'M42', 'M43', 'M44', 'M45', 'M46', 'M47', 'M48', 'M49', 'M5', 'M50', 'M51', 'M52', 'M53', 'M55', 'M56', 'M57', 'M58',
    'M59', 'M60', 'M61', 'M62', 'M63', 'M64', 'M65', 'M66', 'M67', 'M68', 'M69', 'M7', 'M70', 'M71', 'M72', 'M73', 'M74', 'M75', 'M76', 'M77', 'M78',
    'M79', 'M80', 'M81', 'M82', 'M83', 'M84', 'M85', 'M86', 'M87', 'M88', 'M89', 'M9', 'M90', 'M91', 'M92', 'M93', 'M94', 'M95', 'M96', 'M97', 'M98',
    'M99', 'MA', 'MAH', 'MAL', 'MAM', 'MAR', 'MAW', 'MBE', 'MBF', 'MBR', 'MC', 'MCU', 'MD', 'MF', 'MGM', 'MHZ', 'MIK', 'MIL', 'MIN', 'MIO', 'MIU',
    'MK', 'MKD', 'MKM', 'MKW', 'MLD', 'MLT', 'MMK', 'MMQ', 'MMT', 'MND', 'MNJ', 'MON', 'MPA', 'MQ', 'MQD', 'MQH', 'MQM', 'MQS', 'MQW', 'MRD', 'MRM',
    'MRW', 'MSK', 'MT', 'MTK', 'MTQ', 'MTR', 'MTS', 'MTZ', 'MV', 'MVA', 'MWH', 'N1', 'N10', 'N11', 'N12', 'N13', 'N14', 'N15', 'N16', 'N17', 'N18',
    'N19', 'N2', 'N20', 'N21', 'N22', 'N23', 'N24', 'N25', 'N26', 'N27', 'N28', 'N29', 'N3', 'N30', 'N31', 'N32', 'N33', 'N34', 'N35', 'N36', 'N37',
    'N38', 'N39', 'N40', 'N41', 'N42', 'N43', 'N44', 'N45', 'N46', 'N47', 'N48', 'N49', 'N50', 'N51', 'N52', 'N53', 'N54', 'N55', 'N56', 'N57',
    'N58', 'N59', 'N60', 'N61', 'N62', 'N63', 'N64', 'N65', 'N66', 'N67', 'N68', 'N69', 'N70', 'N71', 'N72', 'N73', 'N74', 'N75', 'N76', 'N77',
    'N78', 'N79', 'N80', 'N81', 'N82', 'N83', 'N84', 'N85', 'N86', 'N87', 'N88', 'N89', 'N90', 'N91', 'N92', 'N93', 'N94', 'N95', 'N96', 'N97',
    'N98', 'N99', 'NA', 'NAR', 'NB', 'NBB', 'NC', 'NCL', 'NCR', 'ND', 'NE', 'NEW', 'NF', 'NG', 'NH', 'NI', 'NIL', 'NIU', 'NJ', 'NL', 'NM3', 'NMI',
    'NMP', 'NN', 'NPL', 'NPR', 'NPT', 'NQ', 'NR', 'NRL', 'NT', 'NTT', 'NTU', 'NU', 'NV', 'NX', 'NY', 'OA', 'ODE', 'ODG', 'ODK', 'ODM', 'OHM', 'OMV',
    'ON', 'ONZ', 'OP', 'OPM', 'OT', 'OTB', 'OZ', 'OZA', 'OZI', 'P0', 'P1', 'P10', 'P11', 'P12', 'P13', 'P14', 'P15', 'P16', 'P17', 'P18', 'P19',
    'P2', 'P20', 'P21', 'P22', 'P23', 'P24', 'P25', 'P26', 'P27', 'P28', 'P29', 'P3', 'P30', 'P31', 'P32', 'P33', 'P34', 'P35', 'P36', 'P37', 'P38',
    'P39', 'P4', 'P40', 'P41', 'P42', 'P43', 'P44', 'P45', 'P46', 'P47', 'P48', 'P49', 'P5', 'P50', 'P51', 'P52', 'P53', 'P54', 'P55', 'P56', 'P57',
    'P58', 'P59', 'P6', 'P60', 'P61', 'P62', 'P63', 'P64', 'P65', 'P66', 'P67', 'P68', 'P69', 'P7', 'P70', 'P71', 'P72', 'P73', 'P74', 'P75', 'P76',
    'P77', 'P78', 'P79', 'P8', 'P80', 'P81', 'P82', 'P83', 'P84', 'P85', 'P86', 'P87', 'P88', 'P89', 'P9', 'P90', 'P91', 'P92', 'P93', 'P94', 'P95',
    'P96', 'P97', 'P98', 'P99', 'PA', 'PAL', 'PB', 'PD', 'PE', 'PF', 'PFL', 'PG', 'PGL', 'PI', 'PK', 'PL', 'PLA', 'PM', 'PN', 'PO', 'PQ', 'PR', 'PS',
    'PT', 'PTD', 'PTI', 'PTL', 'PTN', 'PU', 'PV', 'PW', 'PY', 'PZ', 'Q10', 'Q11', 'Q12', 'Q13', 'Q14', 'Q15', 'Q16', 'Q17', 'Q18', 'Q19', 'Q20',
    'Q21', 'Q22', 'Q23', 'Q24', 'Q25', 'Q26', 'Q27', 'Q28', 'Q29', 'Q3', 'Q30', 'Q31', 'Q32', 'Q33', 'Q34', 'Q35', 'Q36', 'Q37', 'Q38', 'Q39', 'Q40',
    'Q41', 'Q42', 'QA', 'QAN', 'QB', 'QD', 'QH', 'QK', 'QR', 'QT', 'QTD', 'QTI', 'QTL', 'QTR', 'R1', 'R4', 'R9', 'RA', 'RD', 'RG', 'RH', 'RK', 'RL',
    'RM', 'RN', 'RO', 'ROM', 'RP', 'RPM', 'RPS', 'RS', 'RT', 'RU', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'SA', 'SAN', 'SCO', 'SCR', 'SD', 'SE', 'SEC',
    'SET', 'SG', 'SHT', 'SIE', 'SK', 'SL', 'SM3', 'SMI', 'SN', 'SO', 'SP', 'SQ', 'SQR', 'SR', 'SS', 'SST', 'ST', 'STC', 'STI', 'STK', 'STL', 'STN',
    'STW', 'SV', 'SW', 'SX', 'SYR', 'T0', 'T1', 'T3', 'T4', 'T5', 'T6', 'T7', 'T8', 'TA', 'TAH', 'TAN', 'TC', 'TD', 'TE', 'TF', 'TI', 'TIC', 'TIP',
    'TJ', 'TK', 'TKM', 'TL', 'TMS', 'TN', 'TNE', 'TP', 'TPI', 'TPR', 'TQ', 'TQD', 'TR', 'TRL', 'TS', 'TSD', 'TSH', 'TST', 'TT', 'TTS', 'TU', 'TV',
    'TW', 'TWH', 'TY', 'U1', 'U2', 'UA', 'UB', 'UC', 'UD', 'UE', 'UF', 'UH', 'UM', 'VA', 'VI', 'VLT', 'VP', 'VQ', 'VS', 'W2', 'W4', 'WA', 'WB',
    'WCD', 'WE', 'WEB', 'WEE', 'WG', 'WH', 'WHR', 'WI', 'WM', 'WR', 'WSD', 'WTT', 'WW', 'X1', 'YDK', 'YDQ', 'YL', 'YRD', 'YT', 'Z1', 'Z11', 'Z2',
    'Z3', 'Z4', 'Z5', 'Z6', 'Z8', 'Z9', 'ZP', 'ZZ'
))

# Common Turkish spellings in ERP exports -> UN/ECE Rec 20 code (all codes are in GIB_UNITS).
UNIT_WORDS = {'adet': 'C62', 'ad': 'C62', 'adt': 'C62', 'ade': 'C62', 'tane': 'C62', 'pcs': 'C62', 'pc': 'C62', 'ea': 'C62', 'kg': 'KGM', 'kilogram': 'KGM', 'kgm': 'KGM',
              'g': 'GRM', 'gr': 'GRM', 'gram': 'GRM', 'ton': 'TNE', 'tn': 'TNE', 'lt': 'LTR', 'l': 'LTR', 'litre': 'LTR', 'ml': 'MLT', 'mililitre': 'MLT',
              'm': 'MTR', 'mt': 'MTR', 'metre': 'MTR', 'cm': 'CMT', 'mm': 'MMT', 'km': 'KMT', 'm2': 'MTK', 'm²': 'MTK', 'metrekare': 'MTK', 'm3': 'MTQ', 'm³': 'MTQ',
              'metreküp': 'MTQ', 'saat': 'HUR', 'sa': 'HUR', 'dk': 'MIN', 'dakika': 'MIN', 'gün': 'DAY', 'gun': 'DAY', 'hafta': 'WEE', 'ay': 'MON', 'yıl': 'ANN', 'yil': 'ANN',
              'paket': 'PA', 'pk': 'PA', 'kutu': 'BX', 'kt': 'BX', 'koli': 'CT', 'karton': 'CT', 'çift': 'PR', 'cift': 'PR', 'set': 'SET', 'takım': 'SET', 'takim': 'SET',
              'düzine': 'DZN', 'duzine': 'DZN', 'rulo': 'RO', 'top': 'RO', 'şişe': 'BO', 'sise': 'BO', 'teneke': 'CA', 'kova': 'BJ', 'torba': 'BG', 'çuval': 'SA',
              'cuval': 'SA', 'palet': 'PF', 'kwh': 'KWH', 'mwh': 'MWH', 'kw': 'KWT', 'sefer': 'C62', 'hizmet': 'C62'}


def unit_code(text, extra=None):
    """'Adet', 'KG', 'koli', 'C62' -> GİB/UNECE unit code, or None. extra: user mapping (lower-case text -> code)."""
    t = text.strip()
    if not t:
        return 'C62'
    low = t.translate(str.maketrans('İIŞĞÜÖÇ', 'iışğüöç')).lower().rstrip('.')
    if extra and low in extra:
        return extra[low]
    # Turkish spellings first: “AD” (adet) and “KG” are also UN/ECE codes (byte, keg) but never mean that in an ERP export.
    if low in UNIT_WORDS:
        return UNIT_WORDS[low]
    return t if t in GIB_UNITS and len(t) >= 2 and t.isupper() else None


def load_unit_map(path):
    """CSV with columns birim, kod (e.g. the birim-kodu-eslestirici agent's output)."""
    if not path:
        return {}
    cols, rows = tabular.read(path)
    if 'birim' not in cols or 'kod' not in cols:
        raise ValueError('--unit-map dosyasında birim ve kod sütunları olmalı.')
    out = {}
    for r in rows:
        code = r['kod'].strip()
        if code not in GIB_UNITS:
            raise ValueError(f'--unit-map: {code} GİB birim kod listesinde yok.')
        out[r['birim'].strip().translate(str.maketrans('İIŞĞÜÖÇ', 'iışğüöç')).lower()] = code
    return out


def party_xml(no, name, tax_office, city, district, postal='', street=''):
    scheme = 'TCKN' if len(no) == 11 else 'VKN'
    person = ''
    if scheme == 'TCKN':
        first, _, last = name.rpartition(' ')
        person = f'<cac:Person><cbc:FirstName>{escape(first or name)}</cbc:FirstName><cbc:FamilyName>{escape(last if first else name)}</cbc:FamilyName></cac:Person>'
    return (f'<cac:Party><cac:PartyIdentification><cbc:ID schemeID="{scheme}">{no}</cbc:ID></cac:PartyIdentification>'
            + (f'<cac:PartyName><cbc:Name>{escape(name)}</cbc:Name></cac:PartyName>' if scheme == 'VKN' else '')
            + '<cac:PostalAddress>' + (f'<cbc:StreetName>{escape(street)}</cbc:StreetName>' if street else '')
            + f'<cbc:CitySubdivisionName>{escape(district or city)}</cbc:CitySubdivisionName><cbc:CityName>{escape(city)}</cbc:CityName>'
            + (f'<cbc:PostalZone>{postal}</cbc:PostalZone>' if postal else '') + '<cac:Country><cbc:Name>Türkiye</cbc:Name></cac:Country></cac:PostalAddress>'
            + (f'<cac:PartyTaxScheme><cac:TaxScheme><cbc:Name>{escape(tax_office)}</cbc:Name></cac:TaxScheme></cac:PartyTaxScheme>' if tax_office else '')
            + person + '</cac:Party>')


PLATE = re.compile(r'^(0[1-9]|[1-7][0-9]|8[01])\s?[A-Z]{1,3}\s?\d{2,5}$')
POSTAL = re.compile(r'^(0[1-9]|[1-7][0-9]|8[01])[0-9]{3}$')


@op('eirsaliye-uret', title='Sevk satırlarından UBL-TR e-İrsaliye XML’i üret (plaka, şoför, posta kodu ve birim kodu denetlenir)', domain='erp',
    product='Muhasebe dışa aktarımları', tag='e-irsaliye', samples={'irsaliye_satirlari.csv': 'irsaliye_satirlari.csv'},
    args=[arg('--seller-no', default='4540536920', help='Gönderen VKN/TCKN'), arg('--seller-name', default='Örnek Satıcı A.Ş.'), arg('--seller-tax-office', default='Büyük Mükellefler'),
          arg('--seller-city', default='İstanbul'), arg('--seller-district', default='Şişli'), arg('--prefix', default='IRS'), arg('--start', type=int, default=1),
          arg('--unit-map', default='', help='Birim eşleme CSV’si (birim, kod); birim-kodu-eslestirici agent’ının çıktısı')],
    description='Sevkiyat satırları tablosundan (alıcı, teslim adresi, plaka, şoför, ürün, miktar, birim, sipariş no) her irsaliye için UBL-TR e-İrsaliye (DespatchAdvice) XML’i '
                'yazar: GİB biçiminde irsaliye numarası, UUID, gönderen ve alıcı, teslim adresi, fiili sevk tarihi/saati, araç plakası, şoför (ad, soyad, TCKN) ve sipariş satırı '
                'referanslı sevk satırları. Plaka biçimi, posta kodu (il koduyla başlayan 5 hane), şoför TCKN’si, VKN/TCKN kontrol hanesi ve birimin GİB birim kod listesinde '
                'olması denetlenir; hatalı satır varsa XML yazılmaz.',
    input='CSV/XLSX (sevk satırları)', output='eirsaliye/<IrsaliyeNo>.xml + sonuc.csv (irsaliye özeti) + hatalar',
    command='python main.py --input sevkiyat.csv --seller-no 1234567890 --prefix SVK --start 501 --unit-map birim_esleme.csv',
    expect={'irsaliye': 2, 'satir_sayisi': 5, 'hatali': 0, 'toplam_miktar': 263.0})
def eirsaliye(columns, rows, args):
    import uuid
    need_cols = ['irsaliye_no', 'tarih', 'alici_no', 'alici_unvan', 'teslim_il', 'teslim_ilce', 'teslim_posta_kodu', 'plaka', 'urun', 'miktar', 'birim']
    for c in need_cols:
        need(c, columns, c)
    if not re.fullmatch(r'[A-Z0-9]{3}', args.prefix):
        raise ValueError('--prefix 3 büyük harf/rakam olmalı.')
    seller_err = tr.vkn(args.seller_no) if len(tr.digits(args.seller_no)) == 10 else tr.tckn(args.seller_no)
    if seller_err:
        raise ValueError(f'Gönderen numarası geçersiz: {seller_err}')
    extra = load_unit_map(args.unit_map)
    style = tabular.number_style([r['miktar'] for r in rows if r['miktar'].strip()])
    groups = defaultdict(list)
    for r in rows:
        groups[r['irsaliye_no'].strip()].append(r)
    files, heads, problems, seq = {}, [], [], args.start
    for key, lines in groups.items():
        first = lines[0]
        issues = []
        d = tabular.parse_date(first['tarih'])
        if not d:
            issues.append(f'tarih okunamadı: {first["tarih"]}')
        when = (first.get('saat', '') or '').strip() or '09:00:00'
        if re.fullmatch(r'\d{2}:\d{2}', when):
            when += ':00'
        if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d:[0-5]\d', when):
            issues.append(f'saat SS:DD biçiminde değil: {when}')
        buyer = tr.digits(first['alici_no'])
        err = tr.vkn(buyer) if len(buyer) == 10 else tr.tckn(buyer) if len(buyer) == 11 else '10 veya 11 hane değil'
        if err:
            issues.append(f'alıcı numarası geçersiz ({err})')
        plate = re.sub(r'\s+', '', first['plaka'].upper())
        if not PLATE.match(plate):
            issues.append(f'plaka biçimi geçersiz: {first["plaka"]}')
        postal = first['teslim_posta_kodu'].strip()
        if not POSTAL.match(postal):
            issues.append(f'posta kodu geçersiz: {postal or "(boş)"} (il koduyla başlayan 5 hane)')
        driver = (first.get('sofor_ad', '').strip(), first.get('sofor_soyad', '').strip(), tr.digits(first.get('sofor_tckn', '')))
        carrier = tr.digits(first.get('tasiyici_no', ''))
        if not any(driver) and not carrier:
            issues.append('şoför veya taşıyıcı firma bilgisi yok')
        if any(driver) and (not all(driver) or tr.tckn(driver[2])):
            issues.append('şoför ad, soyad ve geçerli TCKN birlikte verilmeli')
        line_xml, total = [], 0.0
        for i, r in enumerate(lines, 1):
            qty = tabular.parse_number(r['miktar'], style) if r['miktar'].strip() else None
            code = unit_code(r['birim'], extra)
            if qty is None or qty <= 0:
                issues.append(f'satır {i}: miktar pozitif sayı olmalı ({r["miktar"]})')
                continue
            if not code:
                issues.append(f'satır {i}: birim “{r["birim"]}” GİB birim koduna çevrilemedi (birim-kodu-eslestirici agent’ı veya --unit-map)')
                continue
            total += qty
            item_id = f'<cac:SellersItemIdentification><cbc:ID>{escape(r["urun_kodu"].strip())}</cbc:ID></cac:SellersItemIdentification>' if r.get('urun_kodu', '').strip() else ''
            line_xml.append(f'<cac:DespatchLine><cbc:ID>{i}</cbc:ID><cbc:DeliveredQuantity unitCode="{code}">{qty:g}</cbc:DeliveredQuantity>'
                            f'<cac:OrderLineReference><cbc:LineID>{i}</cbc:LineID></cac:OrderLineReference>'
                            f'<cac:Item><cbc:Name>{escape(r["urun"].strip())}</cbc:Name>{item_id}</cac:Item></cac:DespatchLine>')
        if issues or not d:
            problems += [[key, x] for x in issues]
            heads.append({'irsaliye_no': '', 'kaynak_anahtar': key, 'tarih': first['tarih'], 'alici': first['alici_unvan'].strip(), 'satir': len(lines), 'toplam_miktar': total,
                          'kontrol': '; '.join(issues)})
            continue
        issue = d[0].date()
        doc_id = f'{args.prefix}{issue.year}{seq:09d}'
        seq += 1
        doc_uuid = str(uuid.uuid5(uuid.NAMESPACE_URL, f'eirsaliye/{args.seller_no}/{doc_id}')).upper()
        order = first.get('siparis_no', '').strip()
        driver_xml = (f'<cac:DriverPerson><cbc:FirstName>{escape(driver[0])}</cbc:FirstName><cbc:FamilyName>{escape(driver[1])}</cbc:FamilyName>'
                      f'<cbc:Title>Şoför</cbc:Title><cbc:NationalityID>{driver[2]}</cbc:NationalityID></cac:DriverPerson>') if all(driver) else ''
        carrier_xml = (f'<cac:CarrierParty><cac:PartyIdentification><cbc:ID schemeID="{"TCKN" if len(carrier) == 11 else "VKN"}">{carrier}</cbc:ID></cac:PartyIdentification>'
                       f'<cac:PartyName><cbc:Name>{escape(first.get("tasiyici_unvan", "").strip() or "Taşıyıcı")}</cbc:Name></cac:PartyName>'
                       f'<cac:PostalAddress><cbc:CitySubdivisionName>{escape(first["teslim_ilce"].strip())}</cbc:CitySubdivisionName><cbc:CityName>{escape(first["teslim_il"].strip())}</cbc:CityName>'
                       '<cac:Country><cbc:Name>Türkiye</cbc:Name></cac:Country></cac:PostalAddress></cac:CarrierParty>') if carrier else ''
        xml = ('<?xml version="1.0" encoding="UTF-8"?>\n<DespatchAdvice xmlns="urn:oasis:names:specification:ubl:schema:xsd:DespatchAdvice-2" '
               'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2" '
               'xmlns:ext="urn:oasis:names:specification:ubl:schema:xsd:CommonExtensionComponents-2">'
               '<ext:UBLExtensions><ext:UBLExtension><ext:ExtensionContent><imza:ImzaBekleniyor xmlns:imza="urn:agency-codeblocks:imza"/></ext:ExtensionContent></ext:UBLExtension></ext:UBLExtensions>'
               f'<cbc:UBLVersionID>2.1</cbc:UBLVersionID><cbc:CustomizationID>TR1.2.1</cbc:CustomizationID><cbc:ProfileID>TEMELIRSALIYE</cbc:ProfileID><cbc:ID>{doc_id}</cbc:ID>'
               f'<cbc:CopyIndicator>false</cbc:CopyIndicator><cbc:UUID>{doc_uuid}</cbc:UUID><cbc:IssueDate>{issue.isoformat()}</cbc:IssueDate><cbc:IssueTime>{when}</cbc:IssueTime>'
               '<cbc:DespatchAdviceTypeCode>SEVK</cbc:DespatchAdviceTypeCode>'
               + (f'<cac:OrderReference><cbc:ID>{escape(order)}</cbc:ID><cbc:IssueDate>{issue.isoformat()}</cbc:IssueDate></cac:OrderReference>' if order else '')
               + f'<cac:Signature><cbc:ID schemeID="VKN_TCKN">{args.seller_no}</cbc:ID><cac:SignatoryParty><cac:PartyIdentification><cbc:ID schemeID="'
               f'{"TCKN" if len(args.seller_no) == 11 else "VKN"}">{args.seller_no}</cbc:ID></cac:PartyIdentification><cac:PostalAddress><cbc:CitySubdivisionName>'
               f'{escape(args.seller_district)}</cbc:CitySubdivisionName><cbc:CityName>{escape(args.seller_city)}</cbc:CityName><cac:Country><cbc:Name>Türkiye</cbc:Name></cac:Country>'
               f'</cac:PostalAddress></cac:SignatoryParty><cac:DigitalSignatureAttachment><cac:ExternalReference><cbc:URI>#Signature_{doc_id}</cbc:URI></cac:ExternalReference>'
               '</cac:DigitalSignatureAttachment></cac:Signature>'
               f'<cac:DespatchSupplierParty>{party_xml(args.seller_no, args.seller_name, args.seller_tax_office, args.seller_city, args.seller_district)}</cac:DespatchSupplierParty>'
               f'<cac:DeliveryCustomerParty>{party_xml(buyer, first["alici_unvan"].strip(), first.get("alici_vergi_dairesi", "").strip(), first["teslim_il"].strip(), first["teslim_ilce"].strip(), postal, first.get("teslim_adres", "").strip())}</cac:DeliveryCustomerParty>'
               '<cac:Shipment><cbc:ID/><cac:ShipmentStage><cac:TransportMeans><cac:RoadTransport>'
               f'<cbc:LicensePlateID schemeID="PLAKA">{plate}</cbc:LicensePlateID></cac:RoadTransport></cac:TransportMeans>{driver_xml}</cac:ShipmentStage>'
               '<cac:Delivery><cac:DeliveryAddress>' + (f'<cbc:StreetName>{escape(first.get("teslim_adres", "").strip())}</cbc:StreetName>' if first.get('teslim_adres', '').strip() else '')
               + f'<cbc:CitySubdivisionName>{escape(first["teslim_ilce"].strip())}</cbc:CitySubdivisionName><cbc:CityName>{escape(first["teslim_il"].strip())}</cbc:CityName>'
               f'<cbc:PostalZone>{postal}</cbc:PostalZone><cac:Country><cbc:Name>Türkiye</cbc:Name></cac:Country></cac:DeliveryAddress>{carrier_xml}'
               f'<cac:Despatch><cbc:ActualDespatchDate>{issue.isoformat()}</cbc:ActualDespatchDate><cbc:ActualDespatchTime>{when}</cbc:ActualDespatchTime></cac:Despatch>'
               '</cac:Delivery></cac:Shipment>' + ''.join(line_xml) + '</DespatchAdvice>\n')
        ET.fromstring(xml.encode('utf-8'))
        files[f'eirsaliye/{doc_id}.xml'] = xml
        heads.append({'irsaliye_no': doc_id, 'kaynak_anahtar': key, 'tarih': issue.isoformat(), 'alici': first['alici_unvan'].strip(), 'satir': len(lines),
                      'toplam_miktar': total, 'kontrol': 'tutarlı'})
    bad = sum(h['kontrol'] != 'tutarlı' for h in heads)
    return Result(f'{len(heads) - bad} irsaliye XML’i yazıldı; {bad} irsaliye hatalı olduğu için yazılmadı', list(heads[0]), heads,
                  {'irsaliye': len(heads) - bad, 'satir_sayisi': sum(h['satir'] for h in heads if h['kontrol'] == 'tutarlı'), 'hatali': bad,
                   'toplam_miktar': round(sum(h['toplam_miktar'] for h in heads if h['kontrol'] == 'tutarlı'), 4)}, files=files,
                  tables=[('Hatalar', ['Kaynak irsaliye', 'Sorun'], problems)] if problems else [],
                  notes=['XML’ler imzasızdır; mali mühür ve GİB’e gönderim özel entegratör üzerinden yapılır. Örnek çıktılar geliştirme sırasında GİB UBL-TR 1.2.1 XSD’sinden ve '
                         'GİB Schematron kurallarından hatasız geçti.', 'Matbu irsaliyeden dönüşüm (MATBUDAN), HKS ve İDİS profilleri desteklenmez.'],
                  code=1 if bad and not files else 0)


# KDV tevkifat kodları ve izin verilen tevkif oranları (yüzde) — GİB UBL-TR kod listesi (WithholdingTaxTypeWithPercent)
WITHHOLDING = {
    '601': ('Yapım işleri ile birlikte ifa edilen mühendislik-mimarlık ve etüt-proje hizmetleri', {30, 40}),
    '602': ('Etüt, plan-proje, danışmanlık, denetim ve benzeri hizmetler', {90}),
    '603': ('Makine, teçhizat, demirbaş ve taşıtlara ait tadil, bakım ve onarım hizmetleri', {50, 70}),
    '604': ('Yemek servis hizmeti', {50}), '605': ('Organizasyon hizmeti', {50}), '606': ('İşgücü temin hizmetleri', {90}),
    '607': ('Özel güvenlik hizmeti', {90}), '608': ('Yapı denetim hizmetleri', {90}),
    '609': ('Fason tekstil ve konfeksiyon, çanta ve ayakkabı dikim işleri', {50, 70}),
    '610': ('Turistik mağazalara verilen müşteri bulma / götürme hizmetleri', {90}),
    '611': ('Spor kulüplerinin yayın, reklam ve isim hakkı gelirleri', {90}), '612': ('Temizlik hizmeti', {70, 90}),
    '613': ('Çevre ve bahçe bakım hizmetleri', {70, 90}), '614': ('Servis taşımacılığı hizmeti', {50}),
    '615': ('Her türlü baskı ve basım hizmetleri', {50, 70}), '616': ('5018 sayılı kanun kapsamındaki idarelere yapılan diğer hizmetler', {50}),
    '617': ('Hurda metalden elde edilen külçe teslimleri', {70}), '618': ('Hurda dışı bakır, çinko, alüminyum külçe teslimleri', {70}),
    '619': ('Bakır, çinko, alüminyum ve kurşun ürünlerinin teslimi', {70}), '620': ('İstisnadan vazgeçenlerin hurda ve atık teslimi', {70}),
    '621': ('Metal, plastik, lastik, kauçuk, kâğıt, cam hurda ve atıklardan elde edilen hammadde teslimi', {90}),
    '622': ('Pamuk, tiftik, yün, yapağı, ham post ve deri teslimleri', {90}), '623': ('Ağaç ve orman ürünleri teslimi', {50}),
    '624': ('Yük taşımacılığı hizmeti', {20}), '625': ('Ticari reklam hizmetleri', {30}), '626': ('Diğer teslimler', {20}),
    '627': ('Demir-çelik ürünlerinin teslimi', {40, 50}),
    **{str(c): ('Tam tevkifat', {100}) for c in range(801, 826)},
}
# KDV istisna / muafiyet kodları ve hangi fatura tipinde kullanılabildikleri (GİB Schematron)
EXEMPT_ISTISNA = set('001 101 102 103 104 105 106 107 108 201 202 204 205 206 207 208 209 211 212 213 214 215 216 217 218 219 220 221 223 225 226 227 228 229 230 '
                     '231 232 233 234 235 236 237 238 239 240 241 242 250 301 302 303 304 305 306 307 309 310 311 312 313 314 315 316 317 318 319 320 321 322 323 '
                     '324 325 326 327 328 329 330 331 332 333 334 335 336 337 338 340 341 342 343 344 350 501'.split())
EXEMPT_NAMES = {'351': 'KDV - İstisna Olmayan Diğer', '301': '11/1-a Mal İhracatı', '302': '11/1-a Hizmet İhracatı'}


def withholding_percent(text):
    """'9/10', '%90', '90', '0,9' -> 90; None when empty."""
    t = str(text or '').strip().replace('%', '').replace(' ', '')
    if not t:
        return None
    if '/' in t:
        a, _, b = t.partition('/')
        v = float(a.replace(',', '.')) / float(b.replace(',', '.')) * 100
    else:
        v = float(t.replace(',', '.'))
        v = v * 100 if v <= 1 else v
    return int(round(v))
