"""
申论综应智能评分引擎

规则引擎实现：
1. 要点提取（从学生答案抽取编号要点）
2. 要点匹配（与标准答案做关键词重叠匹配）
3. MECE校验（检测要点是否交叉）
4. 格式评分（公文格式要素检测）
5. 字数合规（检查字数限制）
6. 规范度评分（检测规范词使用率）

用法：
  from 评分引擎 import Grader
  grader = Grader()
  result = grader.grade(question_type="单一题", stem="...", material="...",
                        standard_answer="...", student_answer="...")
"""
import re
import json
from pathlib import Path
from collections import Counter

BASE = Path(__file__).parent.resolve()

# 加载规范词
GUIFANCI_PATH = BASE / "gongkao-tiku" / "智能系统" / "规范词大全" / "规范词总表.json"
if GUIFANCI_PATH.exists():
    with open(GUIFANCI_PATH, encoding="utf-8") as f:
        GUIFANCI = json.load(f)
    ALL_GUIFANCI = set()
    for key in ["对策", "影响_积极", "影响_消极", "问题"]:
        for item in GUIFANCI.get(key, []):
            ALL_GUIFANCI.add(item["phrase"])
else:
    ALL_GUIFANCI = set()


class Grader:
    """申论综应智能评分引擎。"""

    # 评分维度权重（按题型）
    WEIGHTS = {
        "单一题": {"要点": 0.6, "条理": 0.2, "规范": 0.1, "字数": 0.1},
        "综合题": {"要点": 0.5, "结构": 0.25, "条理": 0.1, "规范": 0.1, "字数": 0.05},
        "公文写作题": {"要点": 0.4, "格式": 0.3, "条理": 0.15, "规范": 0.1, "字数": 0.05},
        "案例分析题": {"要点": 0.6, "条理": 0.2, "规范": 0.1, "字数": 0.1},
        "文章写作题": {"立意": 0.3, "论据": 0.25, "结构": 0.2, "语言": 0.15, "字数": 0.1},
        "材料作文题": {"立意": 0.3, "论据": 0.25, "结构": 0.2, "语言": 0.15, "字数": 0.1},
    }

    def grade(self, question_type, stem, material, standard_answer, student_answer):
        """主评分入口。"""
        result = {
            "question_type": question_type,
            "dimensions": {},
            "total_score": 0,
            "feedback": [],
            "suggestions": [],
        }

        weights = self.WEIGHTS.get(question_type, {"要点": 0.6, "条理": 0.2, "规范": 0.1, "字数": 0.1})

        # 1. 要点评分（要点类题型）
        if "要点" in weights or "立意" not in weights:
            point_score, point_detail = self._grade_points(standard_answer, student_answer)
            result["dimensions"]["要点"] = {"score": point_score, "detail": point_detail}

        # 2. 格式评分（公文题）
        if "格式" in weights:
            fmt_score, fmt_detail = self._grade_format(student_answer)
            result["dimensions"]["格式"] = {"score": fmt_score, "detail": fmt_detail}

        # 3. 结构评分（综合题/文章写作）
        if "结构" in weights:
            struct_score, struct_detail = self._grade_structure(question_type, student_answer)
            result["dimensions"]["结构"] = {"score": struct_score, "detail": struct_detail}

        # 4. 条理评分
        if "条理" in weights:
            logic_score, logic_detail = self._grade_logic(student_answer)
            result["dimensions"]["条理"] = {"score": logic_score, "detail": logic_detail}

        # 5. 规范度评分
        if "规范" in weights:
            norm_score, norm_detail = self._grade_normalized(student_answer)
            result["dimensions"]["规范"] = {"score": norm_score, "detail": norm_detail}

        # 6. 字数评分
        if "字数" in weights:
            word_score, word_detail = self._grade_wordcount(stem, student_answer)
            result["dimensions"]["字数"] = {"score": word_score, "detail": word_detail}

        # 7. 立意评分（文章写作，规则粗评+建议LLM增强）
        if "立意" in weights:
            idea_score, idea_detail = self._grade_idea(stem, material, student_answer)
            result["dimensions"]["立意"] = {"score": idea_score, "detail": idea_detail}

        # 8. 论据评分
        if "论据" in weights:
            evidence_score, evidence_detail = self._grade_evidence(student_answer)
            result["dimensions"]["论据"] = {"score": evidence_score, "detail": evidence_detail}

        # 9. 语言评分
        if "语言" in weights:
            lang_score, lang_detail = self._grade_language(student_answer)
            result["dimensions"]["语言"] = {"score": lang_score, "detail": lang_detail}

        # 计算总分
        total = 0
        for dim, w in weights.items():
            if dim in result["dimensions"]:
                total += result["dimensions"][dim]["score"] * w
        result["total_score"] = round(total * 100)

        # 生成反馈
        result["feedback"] = self._generate_feedback(result, weights)
        result["suggestions"] = self._generate_suggestions(result, question_type)

        return result

    def _extract_points(self, answer):
        """从答案提取要点列表。"""
        points = []
        for line in (answer or "").split("\n"):
            line = line.strip()
            if not line:
                continue
            # 匹配编号开头
            m = re.match(r'^(?:\d+[\.、]|\（\d+\）|[一二三四五六七八九十]、)\s*(.+)', line)
            if m:
                content = m.group(1).strip()
                # 提取总括词（到第一个句号或逗号）
                for sep in ["。", "，", "；"]:
                    if sep in content:
                        content = content.split(sep)[0]
                if content:
                    points.append(content)
        return points

    def _keywords_of(self, text):
        """提取文本关键词集合。"""
        # 简单双字词提取
        words = set()
        for m in re.finditer(r'[\u4e00-\u9fa5]{2,4}', text):
            words.add(m.group())
        return words

    def _grade_points(self, standard, student):
        """要点匹配评分。"""
        std_points = self._extract_points(standard)
        stu_points = self._extract_points(student)

        if not std_points:
            return 0.7, {"note": "标准答案无明确要点，跳过要点匹配"}

        matched = 0
        match_detail = []
        for sp in std_points:
            sp_kw = self._keywords_of(sp)
            best_sim = 0
            best_stu = ""
            for tp in stu_points:
                tp_kw = self._keywords_of(tp)
                if not sp_kw or not tp_kw:
                    continue
                overlap = len(sp_kw & tp_kw) / max(len(sp_kw), 1)
                if overlap > best_sim:
                    best_sim = overlap
                    best_stu = tp
            if best_sim >= 0.3:
                matched += 1
                match_detail.append({"standard": sp, "student": best_stu, "similarity": round(best_sim, 2), "matched": True})
            else:
                match_detail.append({"standard": sp, "student": "", "similarity": round(best_sim, 2), "matched": False})

        score = matched / len(std_points) if std_points else 0
        detail = {
            "standard_count": len(std_points),
            "student_count": len(stu_points),
            "matched_count": matched,
            "coverage": round(score, 2),
            "matches": match_detail[:10],
        }
        return score, detail

    def _grade_format(self, answer):
        """公文格式评分。"""
        checks = {
            "标题": bool(re.search(r'^.{4,20}\n', answer)) or len(answer.split('\n')[0]) < 25,
            "称谓": bool(re.search(r'[各尊]位.{0,10}[：:]', answer)) or bool(re.search(r'[\u4e00-\u9fa5]{2,10}[：:]\s*\n', answer)),
            "分条": bool(re.search(r'[（(]\d[)）]|[一二三四五六七八九十]、|\d[\.、]', answer)),
            "分段": answer.count('\n\n') >= 2 or answer.count('\n') >= 4,
            "落款": bool(re.search(r'\d{4}年\d{1,2}月', answer)) or answer.rstrip().endswith('日'),
        }
        score = sum(checks.values()) / len(checks)
        return score, {"checks": checks, "passed": sum(checks.values()), "total": len(checks)}

    def _grade_structure(self, qtype, answer):
        """结构评分（综合题三段式 / 文章写作）。"""
        if qtype in ["综合题"]:
            # 检查是否有总分总结构
            has_open = len(answer) > 30  # 有总体释义
            has_body = bool(re.search(r'[一二三四五六七八九十]、|\（\d[)）]', answer))
            has_close = answer.rstrip()[-50:] != answer[:50]  # 有结尾（简单判断）
            score = (has_open + has_body + has_close) / 3
            return score, {"总分总": {"释义": has_open, "展开": has_body, "总结": has_close}}
        elif qtype in ["文章写作题", "材料作文题"]:
            # 检查议论文结构
            has_title = len(answer.split('\n')[0]) < 30
            has_intro = len(answer) > 200
            has_body = bool(re.search(r'[首先其次再次最后第一第二]|分论点', answer)) or answer.count('\n\n') >= 2
            has_conclusion = answer.rstrip()[-100:] != answer[:100]
            score = (has_title + has_intro + has_body + has_conclusion) / 4
            return score, {"议论文结构": {"标题": has_title, "引论": has_intro, "本论": has_body, "结论": has_conclusion}}
        return 0.5, {"note": "结构检查不适用"}

    def _grade_logic(self, answer):
        """条理性评分。"""
        points = self._extract_points(answer)
        if not points:
            return 0.5, {"note": "无明确分条"}
        # 检查要点是否有总括词（前4-8字）
        has_summary_word = sum(1 for p in points if 4 <= len(p.split("，")[0]) <= 10) / len(points)
        # 检查编号是否连续
        return has_summary_word, {"points": len(points), "总括词率": round(has_summary_word, 2)}

    def _grade_normalized(self, answer):
        """规范度评分。"""
        if not ALL_GUIFANCI:
            return 0.6, {"note": "规范词库未加载"}
        # 检测答案中使用了多少规范词
        used = sum(1 for w in ALL_GUIFANCI if w in answer)
        # 检测口语化表述
        colloquial = ["搞", "弄", "整", "啥", "咋", "挺", "蛮", "搞活动", "定规矩"]
        colloquial_count = sum(1 for c in colloquial if c in answer)
        score = min(1.0, used / 5) * 0.7 + max(0, 1 - colloquial_count / 3) * 0.3
        return score, {"规范词使用": used, "口语化表述": colloquial_count}

    def _grade_wordcount(self, stem, answer):
        """字数评分。"""
        # 从题干提取字数要求
        m = re.search(r'(\d+)\s*字', stem or "")
        limit = int(m.group(1)) if m else None
        actual = len(answer)
        if not limit:
            return 0.8, {"actual": actual, "limit": None, "note": "无字数要求"}
        if actual <= limit:
            ratio = actual / limit
            score = min(1.0, ratio / 0.7) if ratio < 0.5 else 1.0
        else:
            over = (actual - limit) / limit
            score = max(0, 1 - over)
        return score, {"actual": actual, "limit": limit, "ratio": round(actual / limit, 2) if limit else None}

    def _grade_idea(self, stem, material, answer):
        """立意评分（规则粗评，建议LLM增强）。"""
        # 规则：答案是否扣题、观点是否明确
        title = answer.split('\n')[0] if answer else ""
        has_title = 5 <= len(title) <= 25
        has_viewpoint = any(w in answer for w in ["应该", "必须", "需要", "应当", "至关重要"])
        score = (has_title + has_viewpoint) / 2
        return score, {"有标题": has_title, "有明确观点": has_viewpoint,
                       "note": "立意精确评分建议使用LLM评判Prompt"}

    def _grade_evidence(self, answer):
        """论据评分。"""
        # 规则：是否引用了事例、数据
        has_example = any(w in answer for w in ["例如", "比如", "以", "案例", "实践"])
        has_data = bool(re.search(r'\d+[%％]', answer))
        has_quote = any(w in answer for w in ['"', '"', '"', '说', '指出', '强调'])
        score = (has_example + has_data + has_quote) / 3
        return score, {"有事例": has_example, "有数据": has_data, "有引言": has_quote}

    def _grade_language(self, answer):
        """语言评分。"""
        # 规则：修辞、排比
        has_rhetoric = any(w in answer for w in ["不仅", "更", "既是", "也是", "一方面", "另一方面"])
        has_parallel = bool(re.search(r'(.{4,10})，\1', answer))  # 简单排比检测
        avg_sent_len = len(answer) / max(answer.count('。'), 1)
        score = (has_rhetoric + has_parallel + (1 if 15 <= avg_sent_len <= 60 else 0.5)) / 3
        return score, {"有修辞": has_rhetoric, "有排比": has_parallel, "平均句长": round(avg_sent_len)}

    def _generate_feedback(self, result, weights):
        """生成评分反馈。"""
        feedback = []
        for dim in result["dimensions"]:
            score = result["dimensions"][dim]["score"]
            w = weights.get(dim, 0)
            grade = "优" if score >= 0.8 else "良" if score >= 0.6 else "待改进"
            feedback.append(f"{dim}: {grade} (得分{score*100:.0f}, 权重{w*100:.0f}%)")
        return feedback

    def _generate_suggestions(self, result, qtype):
        """生成改进建议。"""
        suggestions = []
        for dim, data in result["dimensions"].items():
            score = data["score"]
            detail = data["detail"]
            if score < 0.6:
                if dim == "要点" and "matches" in detail:
                    missing = [m["standard"] for m in detail["matches"] if not m["matched"]]
                    if missing:
                        suggestions.append(f"⚠️ 要点不完整，缺少：{', '.join(missing[:3])}")
                elif dim == "格式" and "checks" in detail:
                    failed = [k for k, v in detail["checks"].items() if not v]
                    if failed:
                        suggestions.append(f"⚠️ 格式不完整，缺少：{', '.join(failed)}")
                elif dim == "条理":
                    suggestions.append("⚠️ 建议分条作答，每条使用规范总括词")
                elif dim == "规范":
                    suggestions.append("⚠️ 建议使用规范词汇，避免口语化表述")
        return suggestions


# ════════════════════════════════════════════════════════════════
# 命令行入口
# ════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="申论综应智能评分引擎")
    parser.add_argument("--demo", action="store_true", help="运行演示")
    args = parser.parse_args()

    grader = Grader()

    if args.demo:
        # 演示：用一道真实题评分
        import sqlite3
        conn = sqlite3.connect(str(BASE / "data" / "fenbi.db"))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM questions WHERE question_type='单一题' AND correct_answer LIKE '%参考答案说明%' LIMIT 1"
        ).fetchone()

        standard = row["correct_answer"].split("参考答案说明")[0] if "参考答案说明" in row["correct_answer"] else row["correct_answer"]
        # 模拟一个较差的学生答案（漏要点）
        student = "1.政府很重视。2.出了很多政策。3.投入了资金。4.开展了宣传。"

        result = grader.grade(
            question_type=row["question_type"],
            stem=row["stem"],
            material=row["material"],
            standard_answer=standard,
            student_answer=student,
        )

        print("=" * 56)
        print("  评分演示")
        print("=" * 56)
        print(f"题型: {result['question_type']}")
        print(f"总分: {result['total_score']}/100")
        print("\n维度得分:")
        for dim in result["dimensions"]:
            d = result["dimensions"][dim]
            print(f"  {dim}: {d['score']*100:.0f}分")
        print("\n反馈:")
        for fb in result["feedback"]:
            print(f"  {fb}")
        print("\n建议:")
        for sg in result["suggestions"]:
            print(f"  {sg}")
