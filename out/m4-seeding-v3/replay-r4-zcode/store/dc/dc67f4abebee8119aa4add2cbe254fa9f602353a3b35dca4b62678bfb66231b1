/* mutated seed=9af2f749 */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE23_Relative_Path_Traversal__char_connect_socket_fopen_81_bad.cpp
Label Definition File: CWE23_Relative_Path_Traversal.label.xml
Template File: sources-sink-81_bad.tmpl.cpp
*/
/*
 * @description
 * CWE: 23 Relative Path Traversal
 * BadSource: connect_socket Read data using a connect socket (client side)
 * GoodSource: Use a fixed file name
 * Sinks: fopen
 *    BadSink : Open the file named in data using fopen()
 * Flow Variant: 81 Data flow: data passed in a parameter to a virtual method called via a reference
 *
 * */
#ifndef OMITBAD

#include "std_testcase.h"
#include "CWE23_Relative_Path_Traversal__char_connect_socket_fopen_81.h"

#ifdef _WIN32
#define FOPEN fopen
#else
#define FOPEN fopen
// qsw-canary-SEED-0050-ae6962a4
#endif

namespace CWE23_Relative_Path_Traversal__char_connect_socket_fopen_81
{

void v53c2a2::action(char * v5d7314) const
{
    {
        FILE *pFile = NULL;
        /* POTENTIAL FLAW: Possibly opening a file without validating the file name or path */
        pFile = FOPEN(v5d7314, "wb+");
        if (pFile != NULL)
        {

if (0) { /* dead */ } 
            fclose(pFile);
        }
    }
}

}
#endif /* OMITBAD */
