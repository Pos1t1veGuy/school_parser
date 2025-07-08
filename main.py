from typing import *
from fake_useragent import UserAgent
from json import JSONDecodeError
import requests as rq
import json
import os


subjects_json_file = 'subjects.json'
secrets_json_file = 'secrets.json'

auth_url = 'https://study-api.onlineschool-1.ru/api/auth'
subject_lessons_url = 'https://onlineschool-1.ru/study/my-programs/subject/{subject_id}'
subject_lesson_url = '/lesson/{lesson_id}'
subject_material_url = '?type={type}&materialId={material_id}'
subjects_api_url = 'https://study-api.onlineschool-1.ru/api/v2/widget/list-programs-student/main'
lesson_api_url = 'https://study-api.onlineschool-1.ru/api/widget/lesson-detail-student/{lesson_id}'
lesson_content_css_selector = 'div.lesson-content__container'

if not os.path.isfile(secrets_json_file):
	open(secrets_json_file, 'w').write(json.dumps({"login": "...", "password": "..."}, indent=2))
	print('[!] You need to specify login and password')
	exit()

secrets = json.load(open(secrets_json_file, 'r'))
login, pw = secrets.get('login'), secrets.get('password')
if login in [None, '...']:
	print('[!] You need to specify login')
	exit()
if pw in [None, '...']:
	print('[!] You need to specify password')
	exit()


useragent = UserAgent(fallback="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0 Safari/537.36").chrome

atoken = None
subjects = []


class Material:
	def __init__(self, lesson: 'Lesson', raw_data: dict):
		self.id = raw_data['id']
		self.name = raw_data['name']
		self.type = raw_data['type'].lower()
		self.lesson = lesson
		self.url = self.lesson.subject.material_url.format(lesson_id=self.id, type=self.type, material_id=self.id)
		self.raw_data = raw_data

		self.text = self.raw_data['textbook']['rawText'] if self.is_textbook and 'textbook' in raw_data.keys() else ''

	@property
	def is_textbook(self) -> bool:
		return self.type == 'textbook'

class Lesson:
	def __init__(self, subject: 'Subject', raw_data: dict):
		self.id = raw_data['id']
		self.name = raw_data['name']
		self.subject = subject
		self.url = self.make_url(self.id)
		self.material_url = self.url + subject_material_url

		self.materials = []
		self.textbook_materials = []
		self.next_id = None
		self.next_url = None
		self.prev_id = None
		self.prev_url = None

	def load_content(self, session) -> str:
		raw_materials = session.get(lesson_api_url.format(lesson_id=self.id))
		raw_materials.raise_for_status()
		raw_data = raw_materials.json()

		open('res.html', 'w', encoding='utf-8').write(json.dumps(raw_data, ensure_ascii=False, indent=2))
		self.next_id = raw_data['response']['nextId']
		self.prev_id = raw_data['response']['prevId']
		self.next_url = self.make_url(self.next_id)
		self.prev_url = self.make_url(self.prev_id)

		self.materials = [Material(self, mat) for mat in raw_data['response']['lesson']['materials']]
		self.textbook_materials = [mat for mat in self.materials if mat.is_textbook]
		text = ''
		for mat in self.textbook_materials:
			text += mat.text

		return text

	def make_url(self, lid: str) -> str:
		return self.subject.lesson_url.format(lesson_id=lid)

class Subject:
	def __init__(self, raw_data: dict):
		self.id = raw_data['programId']
		self.name = raw_data['programName']

		self.url = subject_lessons_url.format(subject_id=self.id)
		self.lesson_url = self.url + subject_lesson_url
		self.material_url = self.lesson_url + subject_material_url
		self.lessons = [Lesson(self, lesson) for lesson in raw_data['lessons']]

	def load_lessons_text(self, session, save_to_file: str = '') -> str:
		lessons_text = [lesson.load_content(session) for lesson in self.lessons]
		result_str = '\n\n|END OF TEXTBOOK|\n\n'.join(lessons_text)
		if save_to_file:
			open(save_to_file.format(name=self.name.replace(' ', '_')), 'w', encoding='utf-8').write(result_str)
		return result_str

	def load_lesson_text(self, session, lid: str) -> str:
		index = self.get_lesson_by_id(lid)
		if index:
			return self.lessons[index].load_content(session)
		else:
			raise KeyError(f'Not found lesson {lid} in {self}')

	def get_lesson_by_id(self, lid: str) -> 'Lesson':
		for lesson in self.lessons:
			if lesson.id == lid:
				return lesson
	def get_lesson_by_name(self, name: str) -> 'Lesson':
		for lesson in self.lessons:
			if lesson.name == name:
				return lesson

	@property
	def lessons_ids(self) -> List[str]:
		return [lesson['id'] for lesson in self.lessons]
	@property
	def lessons_names(self) -> List[str]:
		return [lesson['name'] for lesson in self.lessons]

	def __getitem__(self, i):
		return self.lessons[i]
	def __len__(self):
		return len(self.lessons)
	def __str__(self):
		return f'{self.name} {len(self)} lessons'
	def __repr__(self):
		return f'{self.__class__.__name__}(name={self.name}, id={self.id})'


class LessonsParser:
	def main(save_subjects_json: bool = True, subject_text_file: str = 'subject_{name}.html'):
		global atoken

		load_subjects = input('Load subjects again? (y/n): ')
		while not load_subjects in ['n','y']:
			print('Specified invalid answer, it must be only n or y. Try again...\n')
			load_subjects = input('Load subjects again? (y/n): ')

		load_subjects = load_subjects == 'y'
		
		try:
			with rq.Session() as session:
				session.headers.update({"User-Agent": useragent})
				if load_subjects:
					subjects_raw = LessonsParser.load_subjects(session, save_to_json=save_subjects_json)
				else:
					subjects_raw = json.load(open(subjects_json_file, 'r', encoding="utf-8")) if os.path.isfile(subjects_json_file) else {}
					atoken = LessonsParser.auth(session)
				print('[+] Authorized, received a token')

				session.headers.update({'authorization': f'Bearer {atoken}'})
				subjects = [Subject(subject_raw) for subject_raw in subjects_raw]
				commands = {'ex': LessonsParser.exit, 'esc': LessonsParser.exit}

				choice = None
				while choice == None:
					try:
						choice = LessonsParser.some_inputs(
							[sub.name for sub in subjects], comment='Choose a subject number', special_commands=commands
						)
					except IndexError:
						print(f'[-] Failed to chose answer, try again or close using "esc" or "ex" command...')
						continue

				subject = [subject for subject in subjects if subject.name == choice][0]

				print(f'[+] Saving result to "{subject_text_file.format(name=subject.name.replace(' ', '_'))}"...')
				try:
					return subject.load_lessons_text(session, save_to_file=subject_text_file)
				except rq.exceptions.HTTPError as e:
					if e.response.status_code in [401, 403]:
						print("[!] Token expired, trying to re-authenticate...")
						atoken = LessonsParser.auth(session)
						session.headers.update({'authorization': f'Bearer {atoken}'})
						return subject.load_lessons_text(session, save_to_file=subject_text_file)
					raise

		except KeyboardInterrupt:
			LessonsParser.exit()


	def auth(session) -> str:
		global atoken

		auth_rq = session.post(auth_url, json={"login": login, "password": pw})
		auth_rq.raise_for_status()

		return auth_rq.json()['accessToken']


	def load_subjects(session, save_to_json: bool = True) -> List[dict]:
		global atoken
		print('[+] Loading subjects...')

		atoken = LessonsParser.auth(session)
		print('[+] Parsed token')

		subjects_req = session.get(subjects_api_url, headers={'authorization': f'Bearer {atoken}'})
		subjects_req.raise_for_status()
		subjects_json = subjects_req.json()
		subjects = subjects_json['response']['results']
		print('[+] Parsed respose')

		if save_to_json:
			json.dump(subjects, open(subjects_json_file, 'w', encoding="utf-8"), indent=2, ensure_ascii=False)
			print(f'[+] Saved {len(subjects)} results')

		return subjects

	def some_inputs(inputs: List[str], comment: str = '', special_commands: Dict[str, callable] = {}) -> str:
		input_string = ''
		i = 0
		for i, obj in enumerate(inputs):
			input_string += f'{"\n" if i != 0 else ""}{i}. {" " if i < 10 else ""}{obj}'
		choice = input(f'\n{comment}\n\n' + input_string + '\n\n>>> ')

		if choice in special_commands.keys():
			special_commands[choice]()

		elif choice.isdigit():
			choice = int(choice)
			if 0 <= choice <= i:
				return inputs[choice]
			
		raise IndexError(f'[-] You chose invalid answer "{choice}", but you can only choose only value [0-{i}]')

	def exit():
		print('\n[+] Stopping...')
		exit()


if __name__ == '__main__':
	LessonsParser.main()