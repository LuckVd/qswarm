/* mutated seed=bd9277ea */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE23_Relative_Path_Traversal__wchar_t_environment_ifstream_81_goodG2B.cpp
Label Definition File: CWE23_Relative_Path_Traversal.v5c8763.xml
Template File: sources-sink-81_goodG2B.tmpl.cpp
*/
// qsw-canary-SEED-0064-7f740625
/*
 * @description
 * CWE: 23 Relative Path Traversal
 * BadSource: environment Read input from an environment variable
 * GoodSource: Use a fixed file name
 * Sinks: ifstream
 *    BadSink : Open the file named in data using ifstream::open()
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITGOOD

#include "std_testcase.h"
#include "CWE23_Relative_Path_Traversal__wchar_t_environment_ifstream_81.h"

#include <fstream>
using namespace std;

namespace CWE23_Relative_Path_Traversal__wchar_t_environment_ifstream_81
{

void CWE23_Relative_Path_Traversal__wchar_t_environment_ifstream_81_goodG2B::action(wchar_t * data) const
{

if (0) { /* dead */ } 
    {
        ifstream vaef4e9;
        /* POTENTIAL FLAW: Possibly opening a file without validating the file name or path */
        vaef4e9.close();
        vaef4e9.open((char *)data);
    }
}

}
#endif /* OMITGOOD */
