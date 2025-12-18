"""
章节匹配器 - 从 ragflow-evaluation-tool 集成
用于判断章节层级关系和匹配有效性
"""
import re
import logging
from typing import Optional, List

logger = logging.getLogger(__name__)


class ChapterMatcher:
    """章节匹配器 - 判断章节层级关系"""
    
    # 中文数字到阿拉伯数字的映射
    CHINESE_NUM_MAP = {
        '零': 0, '一': 1, '二': 2, '三': 3, '四': 4, '五': 5,
        '六': 6, '七': 7, '八': 8, '九': 9, '十': 10,
        '十一': 11, '十二': 12, '十三': 13, '十四': 14, '十五': 15,
        '十六': 16, '十七': 17, '十八': 18, '十九': 19, '二十': 20,
        '二十一': 21, '二十二': 22, '二十三': 23, '二十四': 24, '二十五': 25,
        '二十六': 26, '二十七': 27, '二十八': 28, '二十九': 29, '三十': 30,
        '百': 100, '千': 1000, '万': 10000
    }
    
    @staticmethod
    def remove_english_text(text: str) -> str:
        """移除文本中的英文部分，只保留中文部分"""
        if not text:
            return text
        
        english_keywords = ['Chapter', 'Section', 'Part', 'CHAPTER', 'SECTION', 'PART']
        for keyword in english_keywords:
            idx = text.find(keyword)
            if idx != -1:
                return text[:idx].strip()
        
        pattern = r'\b[A-Za-z]{4,}\b'
        match = re.search(pattern, text)
        if match:
            chinese_part = text[:match.start()].strip()
            if chinese_part:
                return chinese_part
        
        return text.strip()
    
    @staticmethod
    def chinese_to_arabic(chinese_num: str) -> Optional[int]:
        """将中文数字转换为阿拉伯数字"""
        if not chinese_num:
            return None
        
        chinese_num = chinese_num.strip()
        
        if chinese_num.isdigit():
            return int(chinese_num)
        
        if chinese_num in ChapterMatcher.CHINESE_NUM_MAP:
            return ChapterMatcher.CHINESE_NUM_MAP[chinese_num]
        
        if chinese_num.endswith('十'):
            base = chinese_num[:-1]
            if base == '':
                return 10
            if base in ChapterMatcher.CHINESE_NUM_MAP:
                return ChapterMatcher.CHINESE_NUM_MAP[base] * 10
        
        if len(chinese_num) >= 2 and '十' in chinese_num:
            parts = chinese_num.split('十')
            if len(parts) == 2:
                if parts[0] == '':
                    tens = 1
                else:
                    tens = ChapterMatcher.CHINESE_NUM_MAP.get(parts[0], 0)
                ones = ChapterMatcher.CHINESE_NUM_MAP.get(parts[1], 0)
                return tens * 10 + ones
        
        return None
    
    @staticmethod
    def normalize_chapter(chapter: str) -> str:
        """标准化章节格式，提取纯数字章节编号"""
        if not chapter:
            return ""
        
        chapter = str(chapter).strip()
        chapter = ChapterMatcher.remove_english_text(chapter)
        chapter = chapter.rstrip('.')
        
        pattern = r'^(\d+(?:\.\d+)*)'
        match = re.match(pattern, chapter)
        if match:
            return match.group(1)
        
        patterns = [
            r'第[一二三四五六七八九十\d]+章[^第]*',
            r'第[一二三四五六七八九十\d]+章第[一二三四五六七八九十\d]+节',
            r'第[一二三四五六七八九十\d]+节',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, chapter)
            if match:
                matched = match.group(0).rstrip('.')
                matched = ChapterMatcher.remove_english_text(matched)
                return matched
        
        return chapter.rstrip('.')
    
    @staticmethod
    def extract_chapter_info(text: str) -> Optional[str]:
        """从文本中提取章节信息"""
        if not text:
            return None
        
        text_str = str(text).strip()
        if not text_str:
            return None
        
        text_str = ChapterMatcher.remove_english_text(text_str)
        
        # 匹配数字格式
        numeric_parts = []
        i = 0
        while i < len(text_str) and text_str[i].isdigit():
            num_match = re.match(r'^\d+', text_str[i:])
            if num_match:
                num_str = num_match.group(0)
                numeric_parts.append(num_str)
                i += len(num_str)
                
                if i < len(text_str) and text_str[i] == '.':
                    if i + 1 < len(text_str) and text_str[i + 1].isdigit():
                        i += 1
                        continue
                    else:
                        break
                elif i < len(text_str) and text_str[i] in (' ', '\t', '\n'):
                    break
                else:
                    break
            else:
                break
        
        if numeric_parts:
            numeric = '.'.join(numeric_parts)
            if i < len(text_str) and text_str[i] in ('x', 'X') and numeric.endswith('.0'):
                numeric = '.'.join(numeric_parts[:-1])
            return numeric
        
        # 匹配中文格式
        chinese_patterns = [
            r'第[一二三四五六七八九十\d]+章第[一二三四五六七八九十\d]+节',
            r'第[一二三四五六七八九十\d]+章[^第]*',
            r'第[一二三四五六七八九十\d]+节',
            r'([一二三四五六七八九十百千万\d]+)[、，。]',
        ]
        
        for pattern in chinese_patterns:
            match = re.search(pattern, text_str)
            if match:
                matched_text = match.group(0).strip()
                matched_text = ChapterMatcher.remove_english_text(matched_text)
                if '、' in matched_text or '，' in matched_text or '。' in matched_text:
                    num_match = re.search(r'([一二三四五六七八九十百千万\d]+)', matched_text)
                    if num_match:
                        return num_match.group(1).strip()
                return matched_text.rstrip('、，。')
        
        chapter_match = re.search(r'(\d+(?:\.\d+)*|第[一二三四五六七八九十\d]+章|第[一二三四五六七八九十\d]+节|[一二三四五六七八九十百千万\d]+)[、，。]?', text_str)
        if chapter_match:
            chapter = chapter_match.group(1).strip()
            chapter = ChapterMatcher.remove_english_text(chapter)
            if re.match(r'^[一二三四五六七八九十百千万]+$', chapter):
                return chapter
            if re.match(r'^\d+(?:\.\d+)*$', chapter):
                return chapter
            if '第' in chapter and ('章' in chapter or '节' in chapter):
                return chapter.rstrip('、，。')
        
        normalized = ChapterMatcher.normalize_chapter(text_str)
        return normalized if normalized and normalized != text_str.rstrip('.') else None
    
    @staticmethod
    def get_chapter_levels(chapter: str) -> List[int]:
        """将章节编号转换为数字列表，用于比较层级"""
        if not chapter:
            return []
        
        normalized = ChapterMatcher.normalize_chapter(chapter)
        if not normalized:
            return []
        
        if re.match(r'^\d+(?:\.\d+)*$', normalized):
            try:
                return [int(x) for x in normalized.split('.')]
            except (ValueError, AttributeError):
                return []
        
        chapter_match = re.match(r'第([一二三四五六七八九十\d]+)章', normalized)
        if chapter_match:
            num_str = chapter_match.group(1)
            num = ChapterMatcher.chinese_to_arabic(num_str)
            if num is not None:
                return [num]
        
        section_match = re.match(r'第([一二三四五六七八九十\d]+)节', normalized)
        if section_match:
            num_str = section_match.group(1)
            num = ChapterMatcher.chinese_to_arabic(num_str)
            if num is not None:
                return [num]
        
        if re.match(r'^[一二三四五六七八九十百千万]+$', normalized):
            num = ChapterMatcher.chinese_to_arabic(normalized)
            if num is not None:
                return [num]
        
        return []
    
    @staticmethod
    def is_parent_chapter(chapter_a: str, chapter_b: str) -> bool:
        """判断chapter_a是否是chapter_b的父章节"""
        if not chapter_a or not chapter_b:
            return False
        
        chapter_a = ChapterMatcher.normalize_chapter(chapter_a)
        chapter_b = ChapterMatcher.normalize_chapter(chapter_b)
        
        if not chapter_a or not chapter_b:
            return False
        
        if chapter_a == chapter_b:
            return False
        
        levels_a = ChapterMatcher.get_chapter_levels(chapter_a)
        levels_b = ChapterMatcher.get_chapter_levels(chapter_b)
        
        if levels_a and levels_b:
            if len(levels_a) < len(levels_b):
                if levels_a == levels_b[:len(levels_a)]:
                    return True
        
        if levels_a and levels_b:
            if len(levels_a) == 1 and len(levels_b) > 0:
                if levels_a[0] == levels_b[0]:
                    return True
        
        if chapter_b.startswith(chapter_a):
            remaining = chapter_b[len(chapter_a):].strip()
            if remaining and (remaining.startswith('第') or remaining.startswith('.') or remaining.startswith('节')):
                return True
        
        return False
    
    @staticmethod
    def is_valid_match(retrieved_chapter: str, reference_chapter: str) -> bool:
        """判断检索结果是否有效匹配标注章节"""
        if not retrieved_chapter or not reference_chapter:
            return False
        
        retrieved_normalized = ChapterMatcher.normalize_chapter(retrieved_chapter)
        reference_normalized = ChapterMatcher.normalize_chapter(reference_chapter)
        
        if not retrieved_normalized or not reference_normalized:
            return False
        
        if retrieved_normalized == reference_normalized:
            return True
        
        retrieved_levels = ChapterMatcher.get_chapter_levels(retrieved_normalized)
        reference_levels = ChapterMatcher.get_chapter_levels(reference_normalized)
        
        if retrieved_levels and reference_levels:
            if retrieved_levels == reference_levels:
                return True
            
            if len(retrieved_levels) == 1 and len(reference_levels) == 1:
                if retrieved_levels[0] == reference_levels[0]:
                    return True
        
        if ChapterMatcher.is_parent_chapter(retrieved_normalized, reference_normalized):
            return True
        
        if ChapterMatcher.is_parent_chapter(reference_normalized, retrieved_normalized):
            return False
        
        return False

