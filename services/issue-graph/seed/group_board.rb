# frozen_string_literal: true
#
# group_board.rb — создать ГРУППОВУЮ доску «Workflow» на группе graphlab
# со списками по меткам status::todo / doing / review / blocked.
#
# Зачем rails-скрипт, а не REST: в этом GitLab (v19 CE, enterprise:false)
# групповые доски нельзя создать через REST — POST /groups/:id/boards отдаёт
# 404 (фича EE). Чтение GET /groups/:id/boards работает, поэтому фронт доску
# видит. Создаём доску на уровне модели рельсами.
#
# Запуск (из каталога services/issue-graph):
#   docker cp seed/group_board.rb ig-gitlab:/tmp/group_board.rb
#   docker exec ig-gitlab gitlab-rails runner /tmp/group_board.rb
#
# Идемпотентно: доска и списки переиспользуются по имени/метке, не дублируются.

GROUP_PATH  = ENV.fetch("IG_GROUP", "graphlab")
BOARD_NAME  = ENV.fetch("IG_BOARD", "Workflow")
STATUS_LABELS = %w[status::todo status::doing status::review status::blocked].freeze

group = Group.find_by_full_path(GROUP_PATH)
abort("Group '#{GROUP_PATH}' not found") unless group

# Доска группы по имени (идемпотентно).
board = group.boards.find_by(name: BOARD_NAME) || group.boards.create!(name: BOARD_NAME)
puts "board '#{board.name}' (id=#{board.id}) on group '#{group.full_path}'"

# Метки группы status::* — должны существовать (их создаёт seed.py на уровне
# группы). Берём существующие, отсутствующие просто пропускаем с предупреждением.
group_labels = group.labels.where(title: STATUS_LABELS).index_by(&:title)

STATUS_LABELS.each_with_index do |title, position|
  label = group_labels[title]
  unless label
    warn "  ! label '#{title}' not found at group level — skip (run seed.py first)"
    next
  end

  # Список доски по этой метке (идемпотентно).
  list = board.lists.find_by(label_id: label.id)
  if list
    puts "  list '#{title}' exists (id=#{list.id})"
  else
    list = board.lists.create!(label: label, list_type: :label, position: position)
    puts "  + list '#{title}' (id=#{list.id})"
  end
end

puts "DONE. group board '#{BOARD_NAME}' ready."
