import argparse
import asyncio
import hashlib
import os
import time

from editor_actor_agent import EditorActorAgent
from fact_check_agent import FactCheckerAgent
from langchain_openai import AzureOpenAIEmbeddings
from polish_agent import PolishAgent
from ranking_agent import RankingAgent
from research_agent import ResearchAgent
from research_team import ResearchTeam
from reviser_actor_agent import ReviserActorAgent
from tools.intent_detection_tool import IntentDetectionTool
from tools.outline_generation_tool import OutlineGenerationTool
from tools.preprocessing import get_retriver_by_type
from tools.ranking_tool import TextRankingTool
from tools.report_writing_tool import ReportWritingTool
from tools.semantic_citation_tool import SemanticCitationTool
from tools.summarization_tool import TextSummarizationTool
from tools.task_planning_tool import TaskPlanningTool

from erniebot_agent.chat_models import ERNIEBot
from erniebot_agent.extensions.langchain.embeddings import ErnieEmbeddings
from erniebot_agent.memory import SystemMessage
from erniebot_agent.retrieval import BaizhongSearch

parser = argparse.ArgumentParser()
parser.add_argument("--api_type", type=str, default="aistudio")

parser.add_argument(
    "--knowledge_base_name_full_text",
    type=str,
    default="",
    help="The name of the full-text knowledge base(baizhong)",
)
parser.add_argument(
    "--knowledge_base_name_abstract", type=str, default="", help="The name of the abstract base(baizhong)"
)
parser.add_argument(
    "--knowledge_base_id_full_text",
    type=str,
    default="",
    help="The id of the full-text knowledge base(baizhong)",
)
parser.add_argument(
    "--knowledge_base_id_abstract", type=str, default="", help="The id of the abstract base(baizhong)"
)
parser.add_argument(
    "--index_name_full_text", type=str, default="", help="The name of the full-text knowledge base(faiss)"
)
parser.add_argument(
    "--index_name_abstract", type=str, default="", help="The name of the abstract base(faiss)"
)
parser.add_argument(
    "--index_name_citation", type=str, default="citation_index", help="The name of the citation base(faiss)"
)
parser.add_argument("--num_research_agent", type=int, default=2, help="The number of research agent")
parser.add_argument("--save_path", type=str, default="./output/erniebot", help="The report save path")
parser.add_argument("--iterations", type=int, default=4, help="")
parser.add_argument(
    "--report_type",
    type=str,
    default="research_report",
    help="['research_report','resource_report','outline_report']",
)
parser.add_argument(
    "--embedding_type",
    type=str,
    default="openai_embedding",
    help="['openai_embedding','baizhong','ernie_embedding']",
)
parser.add_argument(
    "--framework",
    type=str,
    default="langchain",
    choices=["langchain", "llama_index"],
    help="['langchain','llama_index']",
)
args = parser.parse_args()
os.environ["api_type"] = args.api_type
access_token = os.environ.get("EB_AGENT_ACCESS_TOKEN", None)


def get_retrievers(build_index_function, retrieval_tool):
    if args.embedding_type == "openai_embedding":
        embeddings = AzureOpenAIEmbeddings(azure_deployment="text-embedding-ada")
        fulltext_db = build_index_function(index_name=args.index_name_full_text, embeddings=embeddings)
        abstract_db = build_index_function(index_name=args.index_name_abstract, embeddings=embeddings)
        abstract_search = retrieval_tool(abstract_db, embeddings=embeddings)
        retriever_search = retrieval_tool(fulltext_db, embeddings=embeddings)
    elif args.embedding_type == "ernie_embedding":
        embeddings = ErnieEmbeddings(aistudio_access_token=access_token)
        fulltext_db = build_index_function(index_name=args.index_name_full_text, embeddings=embeddings)
        abstract_db = build_index_function(index_name=args.index_name_abstract, embeddings=embeddings)
        abstract_search = retrieval_tool(abstract_db, embeddings=embeddings)
        retriever_search = retrieval_tool(fulltext_db, embeddings=embeddings)
    elif args.embedding_type == "baizhong":
        embeddings = ErnieEmbeddings(aistudio_access_token=access_token)
        retriever_search = BaizhongSearch(
            access_token=access_token,
            knowledge_base_name=args.knowledge_base_name_full_text,
            knowledge_base_id=args.knowledge_base_id_full_text,
        )
        abstract_search = BaizhongSearch(
            access_token=access_token,
            knowledge_base_name=args.knowledge_base_name_abstract,
            knowledge_base_id=args.knowledge_base_id_abstract,
        )
    return {"full_text": retriever_search, "abstract": abstract_search, "embeddings": embeddings}


def get_tools(llm, llm_long):
    intent_detection_tool = IntentDetectionTool(llm=llm)
    outline_generation_tool = OutlineGenerationTool(llm=llm)
    ranking_tool = TextRankingTool(llm=llm, llm_long=llm_long)
    report_writing_tool = ReportWritingTool(llm=llm, llm_long=llm_long)
    summarization_tool = TextSummarizationTool()
    task_planning_tool = TaskPlanningTool(llm=llm)
    semantic_citation_tool = SemanticCitationTool(theta_min=0.7)

    return {
        "intent_detection": intent_detection_tool,
        "outline": outline_generation_tool,
        "ranking": ranking_tool,
        "report_writing": report_writing_tool,
        "text_summarization": summarization_tool,
        "task_planning": task_planning_tool,
        "semantic_citation": semantic_citation_tool,
    }


def get_agents(retriever_sets, tool_sets, llm, llm_long, build_index_function, retrieval_tool):
    dir_path = f"{args.save_path}/{hashlib.sha1(query.encode()).hexdigest()}"
    os.makedirs(dir_path, exist_ok=True)

    target_path = f"{args.save_path}/{hashlib.sha1(query.encode()).hexdigest()}/revised"
    os.makedirs(target_path, exist_ok=True)
    research_actor = []
    for i in range(args.num_research_agent):
        agents_name = "agent_" + str(i)
        research_agent = ResearchAgent(
            name=agents_name,
            system_message=SystemMessage("你是一个报告生成助手。你可以根据用户的指定内容生成一份报告手稿"),
            dir_path=dir_path,
            report_type=args.report_type,
            retriever_abstract_db=retriever_sets["abstract"],
            retriever_fulltext_db=retriever_sets["full_text"],
            intent_detection_tool=tool_sets["intent_detection"],
            task_planning_tool=tool_sets["task_planning"],
            report_writing_tool=tool_sets["report_writing"],
            outline_tool=tool_sets["outline"],
            summarize_tool=tool_sets["text_summarization"],
            llm=llm,
        )
        research_actor.append(research_agent)
    editor_actor = EditorActorAgent(name="editor", llm=llm, llm_long=llm_long)
    reviser_actor = ReviserActorAgent(name="reviser", llm=llm, llm_long=llm_long)
    polish_actor = PolishAgent(
        name="polish",
        llm=llm,
        llm_long=llm_long,
        citation_tool=tool_sets["semantic_citation"],
        citation_index_name=args.index_name_citation,
        embeddings=retriever_sets["embeddings"],
        dir_path=target_path,
        report_type=args.report_type,
    )
    checker_actor = FactCheckerAgent(name="fact_check", llm=llm, retriever_db=retriever_sets["full_text"])
    ranker_actor = RankingAgent(
        llm=llm,
        llm_long=llm_long,
        name="ranker",
        ranking_tool=tool_sets["ranking"],
        build_index_function=build_index_function,
        search_tool=retrieval_tool,
    )
    return {
        "research_actor": research_actor,
        "editor_actor": editor_actor,
        "reviser_actor": reviser_actor,
        "ranker_actor": ranker_actor,
        "checker_actor": checker_actor,
        "polish_actor": polish_actor,
    }


def main(query):
    llm_long = ERNIEBot(model="ernie-3.5-8k")
    llm = ERNIEBot(model="ernie-4.0")
    build_index_function, retrieval_tool = get_retriver_by_type(args.framework)
    retriever_sets = get_retrievers(build_index_function, retrieval_tool)
    tool_sets = get_tools(llm, llm_long)
    agent_sets = get_agents(retriever_sets, tool_sets, llm, llm_long, build_index_function, retrieval_tool)
    research_team = ResearchTeam(**agent_sets)

    report, file_path = asyncio.run(research_team.run(query))
    print(file_path)
    print(report)


if "__main__" == __name__:
    query = "写一份有关大模型技术发展的报告"
    start_time = time.time()
    main(query)
    end_time = time.time()
    print("Took time: {}".format(end_time - start_time))
