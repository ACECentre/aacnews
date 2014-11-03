import os
import os.path as op
import datetime
from time import gmtime, strftime
import mailchimp

from dateutil.relativedelta import relativedelta
from flask import Flask, redirect, request, url_for, render_template
from flask.ext.sqlalchemy import SQLAlchemy
from flask.ext.admin import expose, helpers

from wtforms import validators

from flask.ext import admin, login
from flask.ext.admin.contrib import sqla
from flask.ext.admin.contrib.sqla import filters

from sqlalchemy import func, and_
from wtforms import form, fields

from collections import defaultdict
from jinja2 import Template


# Create application
app = Flask(__name__)

app.config['SECRET_KEY'] = '123456790'
app.config['DATABASE_FILE'] = 'aacnews_db.sqlite'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + app.config['DATABASE_FILE']
app.config['SQLALCHEMY_ECHO'] = True
app.config['MAILCHIMP_CAMPAIGN_NAME'] = 'AACNews Monthly'
app.config['MAILCHIMP_APIKEY'] = '1425524e7dfb0509136e310f1edbed7f-us7'
app.config['USERNAME'] = 'willwade@gmail.com'
app.config['PASSWORD'] = 'pass'
db = SQLAlchemy(app)


login_manager = login.LoginManager()
login_manager.init_app(app)

@login_manager.user_loader
def load_user(user_id):
    return db.session.query(User).get(user_id)


def get_mailchimp_api():
    return mailchimp.Mailchimp(app.config['MAILCHIMP_APIKEY']) 

# Create models
class Type(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.Unicode(64), unique=True)

    def __unicode__(self):
        return self.name

class Newsletter(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.Unicode(128))
    date = db.Column(db.Date)
    preamble = db.Column(db.Text)
    spoiler = db.Column(db.Unicode(255))
    html = db.Column(db.Text)
    
    def __unicode__(self):
        return self.title


class Post(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    text = db.Column(db.Text, nullable=False)
    date = db.Column(db.Date, default=datetime.date.today())
    link = db.Column(db.Text, nullable=True)
    publish = db.Column(db.Boolean, default=False)

    author = db.Column(db.String(120))
        
    type_id = db.Column(db.Integer(), db.ForeignKey(Type.id))
    type = db.relationship('Type', backref='types')

    def __unicode__(self):
        return self.title


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    login =  db.Column(db.String(120), unique=True)
    password = db.Column(db.String(64))

    # Flask-Login integration
    def is_authenticated(self):
        return True

    def is_active(self):
        return True

    def is_anonymous(self):
        return False

    def get_id(self):
        return self.id

    # Required for administrative interface
    def __unicode__(self):
        return self.login

# Define login and registration forms (for flask-login)
class LoginForm(form.Form):
    login = fields.TextField(validators=[validators.required()])
    password = fields.PasswordField(validators=[validators.required()])

    def validate_login(self, field):
        user = self.get_user()

        if user is None:
            raise validators.ValidationError('Invalid user')

        if user.password != self.password.data:
            raise validators.ValidationError('Invalid password', self.password)


    def get_user(self):
        return db.session.query(User).filter_by(login=self.login.data).first()


# Flask views
@app.route('/')
def index():
    return '<a href="/admin/">Click me to get to Admin!</a>'


# Customized Post model admin
class PostAdmin(sqla.ModelView):

    def is_accessible(self):
        is_accessible = login.current_user and login.current_user.is_authenticated()
        self._create_form_class = self.get_create_form(is_accessible)
        self._edit_form_class = self.get_edit_form(is_accessible)

        return True;

    list_template = 'post_action.html'
    create_template = 'post_create_action.html'

    column_exclude_list = ['text']
    column_sortable_list = ('title', 'author', 'publish', 'date')
    column_labels = dict(title='Post Title', link='URL')

    column_searchable_list = ('title', Type.name)

    column_filters = ('author',
                      'title',
                      'date',
                      filters.FilterLike(Post.title, 'Fixed Title', options=(('test1', 'Test 1'), ('test2', 'Test 2'))))

    form_args = dict(
                    text=dict(label='Big Text', validators=[validators.required()]),
                    url=dict(label='URL')
                )


    def get_edit_form(self, is_accessible = None):
        form = self.scaffold_form()
        if not is_accessible:
            delattr(form, 'date')
            delattr(form, 'publish')

        return form

    def get_create_form(self, is_accessible = None):
        form = self.scaffold_form()
        if not is_accessible:
            delattr(form, 'date')
            delattr(form, 'publish')
        return form

    def __init__(self, session):
        super(PostAdmin, self).__init__(Post, session)


class EmailPreview(sqla.ModelView):
    def get_query(self):
        today = datetime.date.today()
        last_month = today - relativedelta(months=1)
        return self.session.query(self.model).filter(and_(Post.publish, Post.date <= today, Post.date >= last_month))

    def get_count_query(self):
        return self.session.query(func.count('*')).filter(Post.publish)

    def is_accessible(self):
        return login.current_user.is_authenticated()

    list_template = 'email_preview_list.html'

    can_create = False
    can_edit = False
    can_delete = False
    list_row_actions_header = None
    column_descriptions = None

    column_exclude_list = ['text', 'publish', 'edit']
    column_sortable_list = ('type', 'title', 'author', 'date')
    column_labels = dict(title='Post Title')
    column_searchable_list = ('title', Type.name)

    column_filters = ('author',
                      'title',
                      'date',
                      filters.FilterLike(Post.title, 'Fixed Title', options=(('test1', 'Test 1'), ('test2', 'Test 2'))))

    form_args = dict(
                    text=dict(label='Big Text', validators=[validators.required()])
                )

    @expose('/preview/', methods=('GET', 'POST'))
    def email_preview_action(self):
        title = request.form['title']
        spoiler = request.form['spoiler']
        preamble = request.form['preamble'].replace('\n','<br>')
        ids = [int(i) for i in request.form.getlist('rowid')]

        models = Post.query.filter(Post.id.in_(ids)).all()

        groups = defaultdict(list)
        for obj in models:
            groups[obj.type.name].append( obj )

        posts_map = []
        for key in groups:
            entry = {}
            entry['type'] = key
            entry['posts'] = groups[key]
            posts_map.append(entry)


        m = get_mailchimp_api()
        elem = m.campaigns.list({ 'title' : app.config['MAILCHIMP_CAMPAIGN_NAME'], 'exact' : True })

        assert elem['total'] == 1

        cid = elem['data'][0]['id']

        c = m.campaigns.replicate(cid)

        html = m.campaigns.content(c['id'])['html']

        template = Template(html)
        html = template.render(preamble = preamble, title = title, spoiler = spoiler,
            posts_map = posts_map)

        m.campaigns.update(c['id'], 'content', {'html' : html})

        return self.render('email_preview_action.html', template_content = html, title = title,
            spoiler = spoiler, preamble = preamble, cid = c['id'], ids = ids)

    @expose('/send/', methods = ['GET', 'POST'])
    def email_send_action(self):
        title = request.form['title']
        spoiler = request.form['spoiler']
        preamble = request.form['preamble']
        content = request.form['content']
        cid = request.form['cid']
        ids = [int(i) for i in request.form['ids'].encode('utf').replace("[","").replace("]","").split(",")]

        time_string = strftime("%Y-%m-%d %H:%M:%S", gmtime())

        m = get_mailchimp_api()
        campaign_name = '[' + app.config['MAILCHIMP_CAMPAIGN_NAME'] + '] - ' + title
        m.campaigns.update(cid, 'options', {'title' : campaign_name, 'subject' : campaign_name})
        m.campaigns.send(cid)

        posts = self.session.query(Post).filter(Post.id.in_(ids)).all()

        for post in posts:
            post.publish = False

        newsletter = Newsletter()
        newsletter.title = title
        newsletter.date = datetime.datetime.now()
        newsletter.preamble = preamble.replace('<br>','')
        newsletter.spoiler = spoiler

        newsletter.html = content

        self.session.add(newsletter)
        self.session.commit()

        return redirect(url_for('.index_view'))

    @expose('/cancel/', methods = ['GET', 'POST'])
    def email_cancel_action(self):
        cid = request.form['cid']

        m = get_mailchimp_api()
        m.campaigns.delete(cid)

        return redirect(url_for('.index_view'))

class TypeAdmin(sqla.ModelView):

    def is_accessible(self):
        return login.current_user.is_authenticated()

    def __init__(self, session):
        super(TypeAdmin, self).__init__(Type, session)



class NewsletterAdmin(sqla.ModelView):

    column_exclude_list = ['html']



    def is_accessible(self):
        return login.current_user.is_authenticated()

    def __init__(self, session):
        super(NewsletterAdmin, self).__init__(Newsletter, session)


# Customized index view class that handles login & registration
class MyAdminIndexView(admin.AdminIndexView):

    @expose('/')
    def index(self):
        if not login.current_user.is_authenticated():
            return redirect(url_for('.login_view'))
        return super(MyAdminIndexView, self).index()

    @expose('/login/', methods=('GET', 'POST'))
    def login_view(self):
        form = LoginForm(request.form)
        if helpers.validate_form_on_submit(form):
            user = form.get_user()
            login.login_user(user)
        if login.current_user.is_authenticated():
            return redirect(url_for('.index'))
        self._template_args['form'] = form   
        return super(MyAdminIndexView, self).index()

        

    @expose('/logout/')
    def logout_view(self):
        login.logout_user()
        return redirect(url_for('.index'))

    @expose('/error/')
    def internal_server_error_view(self):
        return self.render("500.html")



@app.errorhandler(Exception)
def internal_server_error(e):
    return redirect(url_for('admin.internal_server_error_view'))


# Create admin
admin = admin.Admin(app, name='AACNews', index_view=MyAdminIndexView(), base_template='my_master.html')

admin.add_view(TypeAdmin(db.session))
admin.add_view(NewsletterAdmin(db.session))
admin.add_view(PostAdmin(db.session))
admin.add_view(EmailPreview(Post, db.session, endpoint='emailview', name='Email'))


@app.route('/post', methods=['GET', 'POST'])
def post():
    if(len(request.form) > 0):
        post = Post()
        post.type_id = request.form["type"]
        post.author = request.form["author"]
        post.title = request.form["title"]
        post.link = request.form["link"]
        post.text = request.form["text"]
        db.session.add(post)
        db.session.commit()

    types = []
    types.append({"id":"", "name":"-"})
    for obj in Type.query.all():
        entry = {}
        entry['id'] = obj.id
        entry['name'] = obj.name
        types.append(entry)
    return render_template('post.html', types = types)


def build_sample_db():
    """
    Populate a small db with some example entries.
    """

    import random
    import datetime

    db.drop_all()
    db.create_all()

    # Create sample Types
    type_list = []
    for tmp in ["Videos", "Training", "Projects", "Software Updates", "Other"]:
        type = Type()
        type.name = tmp
        type_list.append(type)
        db.session.add(type)

    # Create sample Posts
    sample_text = [
        {
            'title': "de Finibus Bonorum et Malorum - Part I",
            'content': "Lorem ipsum dolor sit amet, consectetur adipisicing elit, sed do eiusmod tempor \
                        incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud \
                        exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure \
                        dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla pariatur. \
                        Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia deserunt \
                        mollit anim id est laborum."
        },
        {
            'title': "de Finibus Bonorum et Malorum - Part II",
            'content': "Sed ut perspiciatis unde omnis iste natus error sit voluptatem accusantium doloremque \
                        laudantium, totam rem aperiam, eaque ipsa quae ab illo inventore veritatis et quasi architecto \
                        beatae vitae dicta sunt explicabo. Nemo enim ipsam voluptatem quia voluptas sit aspernatur \
                        aut odit aut fugit, sed quia consequuntur magni dolores eos qui ratione voluptatem sequi \
                        nesciunt. Neque porro quisquam est, qui dolorem ipsum quia dolor sit amet, consectetur, \
                        adipisci velit, sed quia non numquam eius modi tempora incidunt ut labore et dolore magnam \
                        aliquam quaerat voluptatem. Ut enim ad minima veniam, quis nostrum exercitationem ullam \
                        corporis suscipit laboriosam, nisi ut aliquid ex ea commodi consequatur? Quis autem vel eum \
                        iure reprehenderit qui in ea voluptate velit esse quam nihil molestiae consequatur, vel illum \
                        qui dolorem eum fugiat quo voluptas nulla pariatur?"
        },
        {
            'title': "de Finibus Bonorum et Malorum - Part III",
            'content': "At vero eos et accusamus et iusto odio dignissimos ducimus qui blanditiis praesentium \
                        voluptatum deleniti atque corrupti quos dolores et quas molestias excepturi sint occaecati \
                        cupiditate non provident, similique sunt in culpa qui officia deserunt mollitia animi, id \
                        est laborum et dolorum fuga. Et harum quidem rerum facilis est et expedita distinctio. Nam \
                        libero tempore, cum soluta nobis est eligendi optio cumque nihil impedit quo minus id quod \
                        maxime placeat facere possimus, omnis voluptas assumenda est, omnis dolor repellendus. \
                        Temporibus autem quibusdam et aut officiis debitis aut rerum necessitatibus saepe eveniet \
                        ut et voluptates repudiandae sint et molestiae non recusandae. Itaque earum rerum hic tenetur \
                        a sapiente delectus, ut aut reiciendis voluptatibus maiores alias consequatur aut perferendis \
                        doloribus asperiores repellat."
        }
    ]

    for user in tmp:
        entry = random.choice(sample_text)  # select text at random
        post = Post()
        post.title = entry['title']
        post.text = entry['content']
        post.author = 'willwade'
        tmp = int(1000*random.random())  # random number between 0 and 1000:
        post.date = datetime.datetime.now() - datetime.timedelta(days=tmp)
        post.type_id = 1     # select a couple of tags at random
        db.session.add(post)

    user = User()
    user.login = app.config['USERNAME']
    user.password = app.config['PASSWORD']

    db.session.add(user)
    
    db.session.commit()
    return

if __name__ == '__main__':

    # Build a sample db on the fly, if one does not exist yet.
    app_dir = op.realpath(os.path.dirname(__file__))
    database_path = op.join(app_dir, app.config['DATABASE_FILE'])
    if not os.path.exists(database_path):
        build_sample_db()

    # Start app
    app.run(debug=True)
