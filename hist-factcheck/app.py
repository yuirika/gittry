import re
import html
from typing import Dict, Any, List, Tuple

import requests
from flask import Flask, jsonify, render_template, request


app = Flask(__name__)


WIKI_SEARCH_URL = "https://ru.wikipedia.org/w/api.php"
WIKI_SUMMARY_URL = "https://ru.wikipedia.org/api/rest_v1/page/summary/"


def extract_years(text: str) -> List[int]:
	matches = re.findall(r"(?<!\d)(1\d{3}|20\d{2})(?!\d)", text)
	return [int(m) for m in matches]


def is_in_scope_pre_21st_century(text: str) -> Tuple[bool, str]:
	years = extract_years(text)
	if any(year >= 2001 for year in years):
		return False, "Запрос вне охвата: приложение покрывает события до XXI века (не включая его)."
	return True, ""


def extract_integers(text: str) -> List[int]:
	return [int(n) for n in re.findall(r"(?<!\d)(\d+)(?!\d)", text)]


def search_wikipedia_titles(query: str, limit: int = 3) -> List[str]:
	params = {
		"action": "query",
		"list": "search",
		"format": "json",
		"srsearch": query,
		"srlimit": limit,
		"srprop": "snippet",
	}
	resp = requests.get(WIKI_SEARCH_URL, params=params, timeout=10)
	resp.raise_for_status()
	data = resp.json()
	return [hit.get("title", "") for hit in data.get("query", {}).get("search", []) if hit.get("title")]


def get_wikipedia_summary(title: str) -> Dict[str, Any]:
	url = WIKI_SUMMARY_URL + requests.utils.quote(title)
	resp = requests.get(url, timeout=10)
	resp.raise_for_status()
	data = resp.json()
	return {
		"title": data.get("title", title),
		"extract": data.get("extract", ""),
		"description": data.get("description", ""),
		"url": data.get("content_urls", {}).get("desktop", {}).get("page")
	}


def normalize_text(text: str) -> str:
	text = html.unescape(text)
	text = re.sub(r"\s+", " ", text).strip()
	return text


def simple_numeric_fact_check(claim: str, source_text: str) -> Tuple[str, str]:
	"""
	Returns (verdict, explanation)
	verdict in {"да", "нет", "неоднозначно"}
	"""
	claim_numbers = extract_integers(claim)
	source_numbers = extract_integers(source_text)

	if not source_text:
		return "неоднозначно", "Недостаточно данных из источников."

	# If the claim contains a number, try to compare
	if claim_numbers:
		# If exact number appears in source, lean "да"
		if any(n in source_numbers for n in claim_numbers):
			return "да", "В источнике обнаружено совпадение числа из утверждения."
		# If source has numbers but they differ, lean "нет" and surface the first number
		if source_numbers:
			first_num = source_numbers[0]
			return "нет", f"В источниках встречается другое число: {first_num}."
		# No numbers in source
		return "неоднозначно", "В источниках не найдено числовых данных для сравнения."

	# For non-numeric claims, we cannot reliably do yes/no here without NLP
	return "неоднозначно", "Для ненумерических утверждений требуется пояснение."


@app.route("/")
def index():
	return render_template("index.html")


@app.route("/api/factcheck", methods=["POST"])
def api_factcheck():
	payload = request.get_json(force=True, silent=True) or {}
	query = (payload.get("query") or "").strip()
	if not query:
		return jsonify({"ok": False, "error": "Введите утверждение для проверки."}), 400

	in_scope, scope_msg = is_in_scope_pre_21st_century(query)
	if not in_scope:
		return jsonify({"ok": False, "error": scope_msg}), 400

	try:
		titles = search_wikipedia_titles(query, limit=3)
		best_summary = None
		for t in titles:
			try:
				summary = get_wikipedia_summary(t)
				if summary.get("extract"):
					best_summary = summary
					break
			except Exception:
				continue

		if not best_summary:
			return jsonify({
				"ok": True,
				"verdict": "неоднозначно",
				"showExplain": True,
				"explanation": "Подходящие статьи не найдены в российских источниках Википедии.",
				"sourceUrl": None,
			}), 200

		source_text = normalize_text(best_summary.get("extract", ""))
		verdict, reason = simple_numeric_fact_check(query, source_text)

		show_explain = verdict != "да" and verdict != "нет"
		# Brief explanation: either the reason, or concise correction using numbers
		explanation = reason
		if verdict == "нет":
			# Attempt to extract first differing number as correction
			nums = extract_integers(source_text)
			if nums:
				explanation = f"Согласно источнику: {nums[0]} (см. ссылку)."

		return jsonify({
			"ok": True,
			"verdict": verdict,
			"showExplain": show_explain,
			"explanation": explanation,
			"sourceUrl": best_summary.get("url"),
			"sourceTitle": best_summary.get("title"),
		}), 200
	except requests.RequestException:
		return jsonify({"ok": False, "error": "Сетевая ошибка при обращении к Википедии."}), 502
	except Exception:
		return jsonify({"ok": False, "error": "Внутренняя ошибка сервера."}), 500


if __name__ == "__main__":
	app.run(host="0.0.0.0", port=8000, debug=True)